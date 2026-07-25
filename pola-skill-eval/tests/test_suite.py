from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from pola_skill_eval.suite import SuiteValidationError, validate_suite


def fixture() -> dict:
    return json.loads(
        (SKILL_ROOT / "tests/fixtures/suite.json").read_text(encoding="utf-8")
    )


class SuiteTests(unittest.TestCase):
    def test_release_fixture_is_valid(self) -> None:
        suite = validate_suite(fixture(), requested_mode="release")
        self.assertEqual("release", suite["mode"])
        self.assertEqual(6, len(suite["cases"]))
        self.assertFalse(suite["warnings"])

    def test_duplicate_ids_are_rejected(self) -> None:
        data = fixture()
        data["cases"][1]["id"] = data["cases"][0]["id"]
        with self.assertRaises(SuiteValidationError) as caught:
            validate_suite(data)
        self.assertIn("duplicated", str(caught.exception))

    def test_grader_path_escape_is_rejected(self) -> None:
        data = fixture()
        data["cases"][0]["graders"].append(
            {
                "type": "file_exists",
                "path": "../outside",
                "severity": "critical",
            }
        )
        with self.assertRaises(SuiteValidationError) as caught:
            validate_suite(data)
        self.assertIn("remain relative", str(caught.exception))

    def test_release_category_coverage_is_required(self) -> None:
        data = fixture()
        data["cases"] = [
            case for case in data["cases"] if case["category"] != "security"
        ]
        with self.assertRaises(SuiteValidationError) as caught:
            validate_suite(data, requested_mode="release")
        self.assertIn("security", str(caught.exception))

    def test_unknown_grader_is_rejected(self) -> None:
        data = copy.deepcopy(fixture())
        data["cases"][0]["graders"] = [{"type": "looks_good"}]
        with self.assertRaises(SuiteValidationError):
            validate_suite(data)

    def test_case_count_is_bounded(self) -> None:
        base = fixture()["cases"][0]
        data = {
            "schema_version": "1.0",
            "name": "too-many-cases",
            "mode": "standard",
            "cases": [
                {**copy.deepcopy(base), "id": f"case-{index}"}
                for index in range(201)
            ],
        }
        with self.assertRaises(SuiteValidationError) as caught:
            validate_suite(data)
        self.assertIn("at most 200", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
