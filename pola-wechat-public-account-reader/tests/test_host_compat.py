"""Host compatibility installer tests.

Module: offline filesystem integration harness
Purpose: verify read-only checks, safe symlink installs and bounded repairs
Created: 2026-07-25
Author: Codex
Dependencies: Python standard library
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "install_hosts.py"
SKILL_NAME = SKILL_ROOT.name
HOST_TARGETS = {
    "codex": Path(".codex") / "skills" / SKILL_NAME,
    "claude-code": Path(".claude") / "skills" / SKILL_NAME,
    "qoder": Path(".qoder") / "skills" / SKILL_NAME,
    "qoder-work": Path(".qoderwork") / "skills" / SKILL_NAME,
}


class HostCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(
        self,
        *arguments: str,
        include_home: bool = True,
        env: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        command = [sys.executable, str(SCRIPT)]
        if include_home:
            command.extend(["--home", str(self.home)])
        command.extend(arguments)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            self.fail(
                f"installer did not emit JSON: {exc}\n"
                f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}"
            )
        return completed, payload

    def target(self, host: str) -> Path:
        return self.home / HOST_TARGETS[host]

    def test_default_check_is_read_only_for_all_hosts(self) -> None:
        completed, payload = self.run_cli()

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(payload["mode"], "check")
        self.assertEqual(payload["status"], "changes_needed")
        self.assertFalse(payload["changed"])
        self.assertEqual(
            [result["host"] for result in payload["results"]],
            ["codex", "claude-code", "qoder", "qoder-work"],
        )
        self.assertTrue(
            all(result["state"] == "missing" for result in payload["results"])
        )
        for relative_target in HOST_TARGETS.values():
            self.assertFalse((self.home / relative_target.parts[0]).exists())

    def test_apply_installs_all_links_and_is_idempotent(self) -> None:
        first, first_payload = self.run_cli("--apply")

        self.assertEqual(first.returncode, 0)
        self.assertEqual(first_payload["status"], "updated")
        self.assertTrue(first_payload["changed"])
        for host in HOST_TARGETS:
            target = self.target(host)
            self.assertTrue(target.is_symlink())
            self.assertEqual(target.resolve(strict=True), SKILL_ROOT)

        second, second_payload = self.run_cli("--apply")
        self.assertEqual(second.returncode, 0)
        self.assertEqual(second_payload["status"], "ready")
        self.assertFalse(second_payload["changed"])
        self.assertTrue(
            all(result["state"] == "ready" for result in second_payload["results"])
        )

    def test_real_directory_refuses_entire_apply_preflight(self) -> None:
        target = self.target("codex")
        target.mkdir(parents=True)
        marker = target / "keep.txt"
        marker.write_text("keep", encoding="utf-8")

        completed, payload = self.run_cli("--apply")

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["status"], "refused")
        self.assertFalse(payload["changed"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertFalse(self.target("claude-code").exists())
        self.assertFalse(self.target("qoder").exists())
        self.assertFalse(self.target("qoder-work").exists())

    def test_valid_different_link_is_refused_without_replacement(self) -> None:
        different = self.home / "different-skill"
        different.mkdir()
        target = self.target("qoder")
        target.parent.mkdir(parents=True)
        target.symlink_to(different, target_is_directory=True)

        completed, payload = self.run_cli(
            "--host",
            "qoder",
            "--apply",
            "--repair-broken",
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["results"][0]["state"], "conflict_symlink")
        self.assertEqual(target.resolve(strict=True), different.resolve(strict=True))

    def test_broken_link_requires_repair_and_only_repairs_managed_name(self) -> None:
        target = self.target("qoder-work")
        target.parent.mkdir(parents=True)
        target.symlink_to(self.home / "missing-skill", target_is_directory=True)
        unrelated = target.parent / "unrelated-broken-link"
        unrelated.symlink_to(self.home / "also-missing", target_is_directory=True)

        refused, refused_payload = self.run_cli(
            "--host",
            "qoder-work",
            "--apply",
        )
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(refused_payload["results"][0]["state"], "broken_symlink")
        self.assertTrue(target.is_symlink())
        self.assertFalse(target.exists())

        repaired, repaired_payload = self.run_cli(
            "--host",
            "qoder-work",
            "--apply",
            "--repair-broken",
        )
        self.assertEqual(repaired.returncode, 0)
        self.assertEqual(repaired_payload["results"][0]["action"], "repaired")
        self.assertEqual(target.resolve(strict=True), SKILL_ROOT)
        self.assertTrue(unrelated.is_symlink())
        self.assertFalse(unrelated.exists())

    def test_repair_flag_without_apply_remains_read_only(self) -> None:
        target = self.target("claude-code")
        target.parent.mkdir(parents=True)
        original_value = str(self.home / "missing")
        target.symlink_to(original_value, target_is_directory=True)

        completed, payload = self.run_cli(
            "--host",
            "claude-code",
            "--repair-broken",
        )

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(payload["status"], "changes_needed")
        self.assertEqual(payload["results"][0]["planned_action"], "repair")
        self.assertEqual(os.readlink(target), original_value)
        self.assertFalse(payload["changed"])

    def test_codex_home_environment_and_explicit_home_precedence(self) -> None:
        process_home = self.home / "process-home"
        codex_home = self.home / "custom-codex-home"
        process_home.mkdir()
        environment = os.environ.copy()
        environment["HOME"] = str(process_home)
        environment["CODEX_HOME"] = str(codex_home)

        env_check, env_payload = self.run_cli(
            "--host",
            "codex",
            include_home=False,
            env=environment,
        )
        self.assertEqual(env_check.returncode, 1)
        self.assertEqual(
            Path(env_payload["results"][0]["target"]),
            (codex_home / "skills" / SKILL_NAME).resolve(strict=False),
        )
        self.assertFalse(codex_home.exists())

        explicit_check, explicit_payload = self.run_cli(
            "--host",
            "codex",
            env=environment,
        )
        self.assertEqual(explicit_check.returncode, 1)
        self.assertEqual(
            Path(explicit_payload["results"][0]["target"]),
            (
                self.home / ".codex" / "skills" / SKILL_NAME
            ).resolve(strict=False),
        )
        self.assertFalse((self.home / ".codex").exists())


if __name__ == "__main__":
    unittest.main()
