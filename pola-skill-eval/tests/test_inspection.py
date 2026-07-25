from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from pola_skill_eval.inspection import inspect_skill


class InspectionTests(unittest.TestCase):
    def test_good_skill_passes(self) -> None:
        report = inspect_skill(SKILL_ROOT / "tests/fixtures/good-skill")
        self.assertEqual("pass", report["status"])
        self.assertEqual("good-skill", report["identity"]["name"])
        self.assertFalse(report["findings"])

    def test_bad_skill_exposes_hard_gates(self) -> None:
        template = SKILL_ROOT / "tests/fixtures/bad-skill-template"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bad-skill"
            root.mkdir()
            for source in template.iterdir():
                target = root / source.name.removesuffix(".txt")
                shutil.copyfile(source, target)
            report = inspect_skill(root, profile="codex")
        codes = {item["code"] for item in report["findings"]}
        self.assertEqual("reject", report["status"])
        self.assertTrue(
            {
                "FRONTMATTER_REQUIRED",
                "NAME_INVALID",
                "PYTHON_SYNTAX",
                "BROAD_DESTRUCTIVE_REMOVE",
                "REFERENCE_MISSING",
            }.issubset(codes)
        )

    @unittest.skipUnless(hasattr(Path, "symlink_to"), "symlink unavailable")
    def test_external_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            outer = Path(temporary)
            root = outer / "linked-skill"
            root.mkdir()
            (root / "SKILL.md").write_text(
                "---\n"
                "name: linked-skill\n"
                "description: Use this fixture to verify a bounded symlink workflow safely.\n"
                "---\n\n"
                "# Linked Skill\n\n## Workflow\n\n1. Validate links.\n",
                encoding="utf-8",
            )
            outside = outer / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            (root / "escape.txt").symlink_to(outside)
            report = inspect_skill(root)
        self.assertEqual("reject", report["status"])
        self.assertIn(
            "SYMLINK_ESCAPE", {item["code"] for item in report["findings"]}
        )

    def test_invalid_utf8_skill_is_rejected_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "utf8-skill"
            root.mkdir()
            (root / "SKILL.md").write_bytes(b"---\nname: utf8-skill\n---\n\xff")
            report = inspect_skill(root)
        self.assertEqual("reject", report["status"])
        self.assertIn(
            "SKILL_UTF8_INVALID", {item["code"] for item in report["findings"]}
        )


if __name__ == "__main__":
    unittest.main()
