"""V3 private source-policy acceptance and regression tests.

Module: V3 private mode harness
Purpose: open explicitly marked sources without weakening technical safety
Created: 2026-07-25
Author: Codex
Dependencies: Python standard library
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from urllib.parse import unquote
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_ROOT.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from pola_wechat_reader.config import load_config, validate_config  # noqa: E402
from pola_wechat_reader.http_client import HttpResponse  # noqa: E402
from pola_wechat_reader.models import ConfigError, SourceError  # noqa: E402
from pola_wechat_reader.reporting import _markdown  # noqa: E402
from pola_wechat_reader.runner import (  # noqa: E402
    _public_url,
    run_monitor,
    select_targets,
)
from pola_wechat_reader.sources import SourceFetcher, _window_filter  # noqa: E402
from wechat_monitor import (  # noqa: E402
    _cron_command,
    _parser as cli_parser,
    _run_command,
    _validate_command,
)


NOW = datetime(2026, 7, 25, 2, 0, tzinfo=timezone.utc)


def base_config() -> dict:
    return {
        "version": 1,
        "defaults": {
            "fetch_content": False,
            "baseline_mode": "backfill_window",
        },
        "targets": [
            {
                "id": "alpha",
                "name": "Alpha",
                "priority": "critical",
                "enabled": True,
                "sources": [
                    {
                        "id": "primary",
                        "type": "rss",
                        "url": "https://feeds.example/primary.xml",
                        "enabled": True,
                    },
                    {
                        "id": "private-feed",
                        "type": "rss",
                        "url": "https://feeds.example/private.xml",
                        "permission_required": True,
                        "enable_in_private_mode": True,
                        "enabled": False,
                    },
                    {
                        "id": "paused-feed",
                        "type": "rss",
                        "url": "https://feeds.example/paused.xml",
                        "fetch_content": False,
                        "fetch_content_in_private_mode": True,
                        "enabled": False,
                    },
                    {
                        "id": "private-sitemap",
                        "type": "sitemap",
                        "url": "https://feeds.example/sitemap.xml.gz",
                        "url_pattern": "^https://feeds\\.example/articles/",
                        "fetch_content": False,
                        "fetch_content_in_private_mode": True,
                        "enabled": True,
                    },
                ],
            },
            {
                "id": "disabled-target",
                "name": "Disabled",
                "enabled": False,
                "sources": [
                    {
                        "id": "private-but-target-disabled",
                        "type": "rss",
                        "url": "https://feeds.example/disabled.xml",
                        "permission_required": True,
                        "enable_in_private_mode": True,
                        "enabled": False,
                    }
                ],
            },
        ],
    }


def rss_payload() -> str:
    return (
        "<rss version='2.0'><channel><title>Fixture</title><item>"
        "<title>Private source article</title>"
        "<link>https://news.example/private-article</link>"
        "<pubDate>Sat, 25 Jul 2026 01:00:00 +0000</pubDate>"
        "<description>Private source summary with enough information.</description>"
        "</item></channel></rss>"
    )


class FakeClient:
    def __init__(self, responses: dict[tuple[str, str], HttpResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs) -> HttpResponse:
        key = (method.upper(), url)
        self.calls.append(key)
        if key not in self.responses:
            raise SourceError("unexpected_request", f"no fixture for {key}")
        return self.responses[key]


class PrivateModeConfigTests(unittest.TestCase):
    def test_enforced_rejects_enabled_unreferenced_source(self) -> None:
        data = base_config()
        private_source = data["targets"][0]["sources"][1]
        private_source["enabled"] = True
        with self.assertRaisesRegex(ConfigError, "permission_reference"):
            validate_config(data)

        normalized, warnings = validate_config(
            data,
            private_use_override=True,
        )
        source = normalized["targets"][0]["sources"][1]
        self.assertTrue(source["enabled"])
        self.assertTrue(source["permission_override_active"])
        self.assertTrue(
            any("private permission override" in item for item in warnings)
        )

    def test_private_mode_only_enables_marked_sources_and_content(self) -> None:
        normalized, warnings = validate_config(
            base_config(),
            private_use_override=True,
        )
        targets = {target["id"]: target for target in normalized["targets"]}
        sources = {
            source["id"]: source for source in targets["alpha"]["sources"]
        }
        self.assertEqual(
            normalized["defaults"]["source_permission_policy"],
            "private_opt_in",
        )
        self.assertTrue(sources["private-feed"]["enabled"])
        self.assertTrue(sources["private-feed"]["enabled_by_private_mode"])
        self.assertFalse(sources["paused-feed"]["enabled"])
        self.assertFalse(
            sources["paused-feed"]["fetch_content_by_private_mode"]
        )
        self.assertTrue(sources["private-sitemap"]["fetch_content"])
        self.assertTrue(
            sources["private-sitemap"]["fetch_content_by_private_mode"]
        )
        self.assertFalse(targets["disabled-target"]["enabled"])
        self.assertFalse(
            any(
                "disabled-target/private-but-target-disabled" in warning
                for warning in warnings
            )
        )

    def test_policy_and_private_booleans_are_strict(self) -> None:
        invalid_policy = base_config()
        invalid_policy["defaults"]["source_permission_policy"] = "anything"
        with self.assertRaisesRegex(ConfigError, "source_permission_policy"):
            validate_config(invalid_policy)

        for field in (
            "enable_in_private_mode",
            "fetch_content_in_private_mode",
        ):
            with self.subTest(field=field):
                invalid_bool = base_config()
                invalid_bool["targets"][0]["sources"][1][field] = "true"
                with self.assertRaises(ConfigError):
                    validate_config(invalid_bool)

    def test_rss_future_tolerance_is_bounded_and_filters_clock_anomalies(
        self,
    ) -> None:
        for source_index in (0, 3):
            for invalid_value in (-1, 25, True, "1"):
                with self.subTest(
                    source_index=source_index,
                    invalid_value=invalid_value,
                ):
                    invalid = base_config()
                    invalid["targets"][0]["sources"][source_index][
                        "max_future_hours"
                    ] = invalid_value
                    with self.assertRaisesRegex(
                        ConfigError,
                        "max_future_hours",
                    ):
                        validate_config(invalid)

        accepted, rejected = _window_filter(
            [
                {
                    "title": "Current item",
                    "url": "https://news.example/current",
                    "published_at": "2026-07-25T02:30:00Z",
                },
                {
                    "title": "Future feed anomaly",
                    "url": "https://news.example/future",
                    "published_at": "2026-07-25T04:01:00Z",
                },
            ],
            now=NOW,
            lookback_hours=24,
            limit=10,
            max_future_hours=1,
        )
        self.assertEqual(rejected, 1)
        self.assertEqual(
            [article["title"] for article in accepted],
            ["Current item"],
        )

        normalized, _ = validate_config(base_config())
        target = normalized["targets"][0]
        source = target["sources"][0]
        source["max_future_hours"] = 1
        future_payload = rss_payload().replace(
            "Sat, 25 Jul 2026 01:00:00 +0000",
            "Sat, 25 Jul 2026 04:01:00 +0000",
        )
        client = FakeClient(
            {
                ("GET", source["url"]): HttpResponse(
                    status=200,
                    url=source["url"],
                    headers={"content-type": "application/rss+xml"},
                    body=future_payload.encode("utf-8"),
                )
            }
        )
        observation, articles = SourceFetcher(
            client,
            normalized["defaults"],
        ).fetch(
            target,
            source,
            now=NOW,
            observed_at="2026-07-25T02:00:00Z",
            lookback_hours=24,
        )
        self.assertEqual(articles, [])
        self.assertEqual(observation.status, "source_unavailable")
        self.assertEqual(
            observation.error_code,
            "source_entries_rejected",
        )
        self.assertEqual(observation.rejected_count, 1)

    def test_private_mode_does_not_weaken_url_or_secret_validation(self) -> None:
        for unsafe_url in (
            "https://127.0.0.1/private.xml",
            "https://user:password@feeds.example/private.xml",
            "https://feeds.example/private.xml?token=inline-secret",
            "https://feeds.example/private.xml?password=inline-secret",
            "https://feeds.example/private.xml?client_secret=inline-secret",
            "https://feeds.example/private.xml?access-token=inline-secret",
            "https://feeds.example/private.xml?authToken=inline-secret",
            "https://feeds.example/private.xml?jwt=inline-secret",
        ):
            with self.subTest(url=unsafe_url):
                data = base_config()
                data["defaults"]["source_permission_policy"] = "private_opt_in"
                data["targets"][0]["sources"][1]["url"] = unsafe_url
                with self.assertRaises(ConfigError):
                    validate_config(data)
                with self.assertRaises(ValueError):
                    _public_url(unsafe_url, "candidate URL")

    def test_machineheart_safe_and_private_assets_have_distinct_effective_modes(
        self,
    ) -> None:
        safe, _ = load_config(
            SKILL_ROOT / "assets" / "targets.machineheart.json"
        )
        self.assertEqual(
            safe["defaults"]["source_permission_policy"], "enforced"
        )
        self.assertEqual(
            sum(source["enabled"] for source in safe["targets"][0]["sources"]),
            1,
        )

        private, warnings = load_config(
            SKILL_ROOT / "assets" / "targets.machineheart.private.json"
        )
        self.assertEqual(
            private["defaults"]["source_permission_policy"],
            "private_opt_in",
        )
        self.assertEqual(
            sum(
                source["enabled"]
                for source in private["targets"][0]["sources"]
            ),
            3,
        )
        private_target = private["targets"][0]
        self.assertEqual(private_target["biz"], "MzA3MzI4MjgzMw==")
        private_sources = {
            source["id"]: source for source in private_target["sources"]
        }
        xinfinite = private_sources["xinfinite-permission-gated"]
        self.assertEqual(
            xinfinite["entry_link_mode"],
            "wechat_original_from_description",
        )
        self.assertEqual(xinfinite["expected_author"], "机器之心")
        self.assertEqual(xinfinite["max_future_hours"], 1)
        self.assertTrue(
            any("private permission override" in item for item in warnings)
        )
        selected, metadata = select_targets(private)
        self.assertEqual(len(selected["targets"]), 1)
        self.assertEqual(
            sorted(metadata["private_mode_enabled_sources"]),
            [
                "machineheart/machineheart-official-rss",
                "machineheart/xinfinite-permission-gated",
            ],
        )
        self.assertEqual(
            metadata["private_mode_content_sources"],
            ["machineheart/machineheart-official-sitemap"],
        )
        self.assertEqual(len(metadata["permission_overrides"]), 2)

        batch_private, _ = load_config(
            SKILL_ROOT
            / "assets"
            / "targets.batch.private.example.json"
        )
        self.assertEqual(
            batch_private["defaults"]["source_permission_policy"],
            "private_opt_in",
        )
        batch_targets = {
            target["id"]: target for target in batch_private["targets"]
        }
        self.assertEqual(
            sum(
                source["enabled"]
                for source in batch_targets["machineheart"]["sources"]
            ),
            3,
        )
        self.assertFalse(batch_targets["instachina"]["enabled"])

    def test_cli_accepts_private_use_for_all_commands(self) -> None:
        cases = [
            ["validate", "--config", "/tmp/config.json", "--private-use"],
            [
                "run",
                "--config",
                "/tmp/config.json",
                "--state",
                "/tmp/state.sqlite",
                "--output",
                "/tmp/output",
                "--private-use",
            ],
            [
                "cron",
                "--config",
                "/tmp/config.json",
                "--state",
                "/tmp/state.sqlite",
                "--output",
                "/tmp/output",
                "--private-use",
            ],
        ]
        for argv in cases:
            with self.subTest(command=argv[0]):
                self.assertTrue(cli_parser().parse_args(argv).private_use)

    def test_validate_and_cron_expose_private_mode(self) -> None:
        config_path = str(
            SKILL_ROOT / "assets" / "targets.machineheart.json"
        )
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                _validate_command(config_path, private_use=True),
                0,
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(
            payload["source_permission_policy"], "private_opt_in"
        )
        self.assertEqual(len(payload["private_mode_enabled_sources"]), 2)
        self.assertEqual(len(payload["permission_overrides"]), 2)

        args = Namespace(
            config=config_path,
            state="/tmp/pola-private-state.sqlite",
            output="/tmp/pola-private-output",
            schedule="17 * * * *",
            lookback_hours=72,
            private_use=True,
            target_ids=["machineheart"],
            exclude_target_ids=[],
            priorities=[],
        )
        cron_output = StringIO()
        with redirect_stdout(cron_output):
            self.assertEqual(_cron_command(args), 0)
        cron_line = cron_output.getvalue().splitlines()[0]
        self.assertEqual(cron_line.split().count("--private-use"), 1)

    def test_run_command_propagates_private_use_to_runtime(self) -> None:
        config_path = str(
            SKILL_ROOT / "assets" / "targets.machineheart.json"
        )
        args = Namespace(
            config=config_path,
            state="/tmp/pola-private-state.sqlite",
            output="/tmp/pola-private-output",
            dry_run=True,
            lookback_hours=72,
            backfill=True,
            private_use=True,
            target_ids=["machineheart"],
            exclude_target_ids=[],
            priorities=[],
        )
        result = {
            "report": {
                "run_id": "private-cli-fixture",
                "run_status": "degraded",
                "coverage": "partial",
                "counts": {"new": 0, "source_failures": 1},
                "selection": {
                    "selected_target_count": 1,
                    "source_permission_policy": "private_opt_in",
                },
            },
            "json_path": "/tmp/private-report.json",
            "markdown_path": "/tmp/private-report.md",
            "exit_code": 2,
        }
        output = StringIO()
        with (
            patch(
                "wechat_monitor.load_config",
                wraps=load_config,
            ) as loader,
            patch(
                "wechat_monitor.run_monitor",
                return_value=result,
            ) as monitor,
            redirect_stdout(output),
        ):
            self.assertEqual(_run_command(args), 2)
        loader.assert_called_once_with(
            config_path,
            private_use_override=True,
        )
        loaded_config, _ = monitor.call_args.kwargs["loaded_config"]
        self.assertEqual(
            loaded_config["defaults"]["source_permission_policy"],
            "private_opt_in",
        )
        self.assertEqual(
            sum(
                source["enabled"]
                for source in loaded_config["targets"][0]["sources"]
            ),
            3,
        )
        self.assertTrue(monitor.call_args.kwargs["private_use"])
        self.assertEqual(
            json.loads(output.getvalue())["source_permission_policy"],
            "private_opt_in",
        )


class PrivateModeRuntimeTests(unittest.TestCase):
    def test_missing_private_rss_token_isolated_from_public_feed(self) -> None:
        official_url = "https://feeds.example/official.xml"
        public_url = "https://feeds.example/public.xml"
        data = {
            "version": 1,
            "defaults": {
                "source_permission_policy": "private_opt_in",
                "fetch_content": False,
                "baseline_mode": "backfill_window",
            },
            "targets": [
                {
                    "id": "alpha",
                    "name": "Alpha",
                    "priority": "critical",
                    "sources": [
                        {
                            "id": "official",
                            "type": "rss",
                            "url": official_url,
                            "query_from_env": {
                                "token": "POLA_PRIVATE_TEST_TOKEN"
                            },
                            "permission_required": True,
                            "enable_in_private_mode": True,
                            "enabled": False,
                        },
                        {
                            "id": "public",
                            "type": "rss",
                            "url": public_url,
                            "permission_required": True,
                            "enable_in_private_mode": True,
                            "enabled": False,
                        },
                    ],
                }
            ],
        }
        loaded = validate_config(data)
        client = FakeClient(
            {
                ("GET", public_url): HttpResponse(
                    status=200,
                    url=public_url,
                    headers={"content-type": "application/rss+xml"},
                    body=rss_payload().encode("utf-8"),
                )
            }
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {}, clear=True),
        ):
            result = run_monitor(
                config_path=str(Path(directory) / "config.json"),
                state_path=str(Path(directory) / "state.sqlite"),
                output_dir=str(Path(directory) / "output"),
                dry_run=True,
                backfill=True,
                loaded_config=loaded,
                client=client,
                clock=lambda: NOW,
            )
        report = result["report"]
        self.assertEqual(report["run_status"], "degraded")
        self.assertEqual(report["counts"]["candidates"], 1)
        self.assertEqual(report["counts"]["new"], 1)
        self.assertEqual(report["counts"]["source_failures"], 1)
        self.assertEqual(
            report["errors"][0]["code"], "missing_secret"
        )
        self.assertEqual(client.calls, [("GET", public_url)])
        self.assertEqual(
            report["selection"]["source_permission_policy"],
            "private_opt_in",
        )
        self.assertEqual(
            len(report["selection"]["permission_overrides"]), 2
        )
        markdown = _markdown(report)
        self.assertIn("`private_opt_in`", markdown)
        self.assertIn(
            "私人模式启用来源：2（`alpha/official`、`alpha/public`）",
            markdown,
        )
        self.assertIn("私人模式正文来源：0（无）", markdown)
        self.assertIn(
            "Permission override：2（`alpha/official`、`alpha/public`）",
            markdown,
        )


class PrivateModeDocumentationTests(unittest.TestCase):
    def test_v3_artifacts_and_local_markdown_links_exist(self) -> None:
        artifact_paths = [
            PROJECT_ROOT
            / "docs/pola/project-knowledge/requirements"
            / "2026-07-25-pola-wechat-public-account-reader-v3-private-mode.md",
            PROJECT_ROOT
            / "docs/pola/project-knowledge/specs"
            / "2026-07-25-pola-wechat-public-account-reader-v3-private-mode-prd.md",
            PROJECT_ROOT
            / "docs/pola/project-knowledge/architecture"
            / "2026-07-25-pola-wechat-public-account-reader-v3-private-mode-sdd.md",
            PROJECT_ROOT
            / "docs/pola/project-knowledge/delivery"
            / "pola-wechat-public-account-reader/function_test_cases_v3.json",
            PROJECT_ROOT
            / "docs/pola/project-knowledge/test-reports"
            / "2026-07-25-pola-wechat-public-account-reader-v3-private-mode.md",
            PROJECT_ROOT
            / "docs/pola/project-knowledge/devlogs"
            / "2026-07-25-pola-wechat-public-account-reader-v3-private-mode.md",
        ]
        for artifact_path in artifact_paths:
            with self.subTest(artifact=str(artifact_path)):
                self.assertTrue(artifact_path.is_file())
                self.assertGreater(artifact_path.stat().st_size, 100)

        markdown_files = [
            PROJECT_ROOT / "README.md",
            SKILL_ROOT / "README.md",
            SKILL_ROOT / "SKILL.md",
            *sorted((SKILL_ROOT / "references").rglob("*.md")),
            *[path for path in artifact_paths if path.suffix == ".md"],
        ]
        link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
        for markdown_path in markdown_files:
            text = markdown_path.read_text(encoding="utf-8")
            for raw_target in link_pattern.findall(text):
                target = raw_target.strip().strip("<>").split("#", 1)[0]
                if (
                    not target
                    or target.startswith(
                        ("https://", "http://", "mailto:", "app://")
                    )
                ):
                    continue
                linked_path = (
                    markdown_path.parent / unquote(target)
                ).resolve()
                with self.subTest(
                    markdown=str(markdown_path),
                    target=raw_target,
                ):
                    self.assertTrue(linked_path.exists())


if __name__ == "__main__":
    unittest.main()
