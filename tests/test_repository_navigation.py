"""Check relative file links in BMS's entry-point READMEs and canonical docs.

No network access, anchor validation, or historical-plan/specification crawl.
"""
from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


class RepositoryNavigationTest(unittest.TestCase):
    def check_links(self, relative_path: str) -> None:
        document = ROOT / relative_path
        for target in LINK.findall(document.read_text(encoding="utf-8")):
            url = urlsplit(target)
            if url.scheme or url.netloc or not url.path:
                continue
            with self.subTest(document=relative_path, target=target):
                self.assertTrue((document.parent / unquote(url.path)).exists(),
                                f"Broken repository navigation link: {relative_path} -> {target}")

    def test_repository_readme(self) -> None:
        self.check_links("README.md")

    def test_documentation_index(self) -> None:
        self.check_links("docs/README.md")

    def test_api_readme(self) -> None:
        self.check_links("platform/api/README.md")

    def test_frontend_readme(self) -> None:
        self.check_links("platform/frontend/README.md")

    def test_desktop_readme(self) -> None:
        self.check_links("platform/desktop-electron/README.md")

    def test_mobile_readme(self) -> None:
        self.check_links("platform/mobile-cordova/README.md")

    def test_canonical_documentation(self) -> None:
        for document in sorted((ROOT / "docs").glob("*.md")):
            self.check_links(str(document.relative_to(ROOT)))

    def test_artifact_readme(self) -> None:
        self.check_links("artifacts/android/README.md")

    def test_service_readmes_use_explicit_managed_development(self) -> None:
        for relative_path in ("README.md", "platform/api/README.md",
                              "platform/frontend/README.md"):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            with self.subTest(document=relative_path):
                self.assertIn("./start_ui.sh start --runtime dev", source)
                self.assertIn("./start_ui.sh status --runtime dev", source)
                self.assertNotIn("uv run uvicorn", source)
                self.assertNotIn("corepack pnpm install", source)


if __name__ == "__main__":
    unittest.main()
