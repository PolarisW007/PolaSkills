"""V2 batch, source, report and security acceptance tests.

Module: V2 offline acceptance harness
Purpose: prove bounded multi-target monitoring and public-source normalization
Created: 2026-07-25
Author: Codex
Dependencies: Python standard library
"""

from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import MagicMock, patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from pola_wechat_reader.config import load_config, validate_config  # noqa: E402
from pola_wechat_reader.http_client import HttpClient, HttpResponse  # noqa: E402
from pola_wechat_reader.identity import canonicalize_url  # noqa: E402
from pola_wechat_reader.models import (  # noqa: E402
    Article,
    ConfigError,
    SourceError,
    SourceObservation,
)
from pola_wechat_reader.parsers import (  # noqa: E402
    classify_blocked_page,
    parse_public_account_feed,
    parse_sitemap,
)
from pola_wechat_reader.reporting import _markdown, build_report  # noqa: E402
from pola_wechat_reader.runner import (  # noqa: E402
    _normalize_candidate,
    run_monitor,
    select_targets,
)
from pola_wechat_reader.sources import SourceFetcher  # noqa: E402
from wechat_monitor import _parser as cli_parser  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)


def http_response(url: str, body: str, content_type: str) -> HttpResponse:
    return HttpResponse(
        status=200,
        url=url,
        headers={"content-type": content_type},
        body=body.encode("utf-8"),
    )


def rss(entries: list[tuple[str, str, str]]) -> str:
    items = "".join(
        (
            "<item>"
            f"<title>{title}</title>"
            f"<link>{url}</link>"
            f"<pubDate>{published}</pubDate>"
            f"<description>Summary for {title}</description>"
            "</item>"
        )
        for title, url, published in entries
    )
    return f"<rss version='2.0'><channel><title>Fixture</title>{items}</channel></rss>"


class FakeClient:
    def __init__(
        self, responses: dict[tuple[str, str], HttpResponse | Exception]
    ) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs) -> HttpResponse:
        key = (method.upper(), url)
        self.calls.append(key)
        result = self.responses.get(key)
        if result is None:
            raise SourceError("unexpected_request", f"no fixture for {key}")
        if isinstance(result, Exception):
            raise result
        return result


class V2ConfigAndSelectionTests(unittest.TestCase):
    def rss_config(self) -> dict:
        return {
            "version": 1,
            "targets": [
                {
                    "id": "alpha",
                    "name": "Alpha",
                    "priority": "critical",
                    "sources": [
                        {
                            "id": "feed",
                            "type": "rss",
                            "url": "https://feeds.example/alpha.xml",
                        }
                    ],
                }
            ],
        }

    def test_boolean_fields_are_strict_not_truthy_strings(self) -> None:
        mutations = [
            lambda data: data["targets"][0].update(enabled="false"),
            lambda data: data["targets"][0]["sources"][0].update(enabled="false"),
            lambda data: data["targets"][0]["sources"][0].update(
                fetch_content="false"
            ),
            lambda data: data["targets"][0]["sources"][0].update(
                permission_required="false"
            ),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                data = self.rss_config()
                mutate(data)
                with self.assertRaises(ConfigError):
                    validate_config(data)

        sitemap = self.rss_config()
        sitemap["targets"][0]["sources"] = [
            {
                "type": "sitemap",
                "url": "https://news.example/sitemap.xml.gz",
                "url_pattern": r"/article/",
                "nested_https_url": "false",
            }
        ]
        with self.assertRaises(ConfigError):
            validate_config(sitemap)

    def test_duplicate_source_ids_are_rejected_per_target(self) -> None:
        data = self.rss_config()
        data["targets"][0]["sources"].append(
            {
                "id": "feed",
                "type": "rss",
                "url": "https://feeds.example/backup.xml",
            }
        )
        with self.assertRaisesRegex(ConfigError, "duplicate source ids"):
            validate_config(data)

    def test_target_filters_compose_and_do_not_mutate_config(self) -> None:
        data = self.rss_config()
        data["targets"].extend(
            [
                {
                    "id": "beta",
                    "name": "Beta",
                    "priority": "normal",
                    "sources": [
                        {
                            "type": "rss",
                            "url": "https://feeds.example/beta.xml",
                        }
                    ],
                },
                {
                    "id": "gamma",
                    "name": "Gamma",
                    "priority": "critical",
                    "enabled": False,
                    "sources": [],
                },
            ]
        )
        normalized, _ = validate_config(data)
        original_ids = [target["id"] for target in normalized["targets"]]
        selected, metadata = select_targets(
            normalized,
            target_ids=["alpha", "beta"],
            exclude_target_ids=["beta"],
            priorities=["critical"],
        )
        self.assertEqual(
            [target["id"] for target in selected["targets"]], ["alpha"]
        )
        self.assertEqual(metadata["selected_target_ids"], ["alpha"])
        self.assertEqual(metadata["configured_target_count"], 3)
        self.assertEqual(
            [target["id"] for target in normalized["targets"]], original_ids
        )

        with self.assertRaisesRegex(ConfigError, "unknown target ids"):
            select_targets(normalized, target_ids=["missing"])
        with self.assertRaisesRegex(ConfigError, "disabled"):
            select_targets(normalized, target_ids=["gamma"])
        with self.assertRaisesRegex(ConfigError, "selected no enabled targets"):
            select_targets(normalized, priorities=["low"])

    def test_unsafe_candidate_urls_are_rejected_before_storage(self) -> None:
        normalized, _ = validate_config(self.rss_config())
        target = normalized["targets"][0]
        source = target["sources"][0]
        for unsafe in (
            "javascript:alert(document.domain)",
            "https://127.0.0.1/private",
            "https://localhost/article",
            "https://user:password@news.example/article",
            "https://news.example>/article",
            "https://news.exa mple/article",
            "https://news.example|evil/article",
            "https://news.example`evil/article",
        ):
            with self.subTest(url=unsafe):
                with self.assertRaises(ValueError):
                    _normalize_candidate(
                        target,
                        source,
                        {"title": "Unsafe", "url": unsafe},
                        "2026-07-24T08:00:00Z",
                    )

        leaked_value = "must-never-enter-report-or-error"
        with self.assertRaises(ValueError) as raised:
            _normalize_candidate(
                target,
                source,
                {
                    "title": "Secret-bearing URL",
                    "url": (
                        "https://news.example/article?"
                        f"token={leaked_value}"
                    ),
                },
                "2026-07-24T08:00:00Z",
            )
        self.assertNotIn(leaked_value, str(raised.exception))

    def test_public_ipv6_candidate_keeps_brackets_when_canonicalized(self) -> None:
        value = "https://[2606:4700:4700::1111]/article?b=2&a=1"
        self.assertEqual(
            canonicalize_url(value),
            "https://[2606:4700:4700::1111]/article?a=1&b=2",
        )
        normalized, _ = validate_config(self.rss_config())
        article = _normalize_candidate(
            normalized["targets"][0],
            normalized["targets"][0]["sources"][0],
            {"title": "IPv6", "url": value},
            "2026-07-24T08:00:00Z",
        )
        self.assertEqual(article.url, canonicalize_url(value))

    def test_non_wechat_host_cannot_forge_verified_identity_or_wx_key(self) -> None:
        data = self.rss_config()
        data["targets"][0]["biz"] = "MzTargetBiz=="
        normalized, _ = validate_config(data)
        target = normalized["targets"][0]
        article = _normalize_candidate(
            target,
            target["sources"][0],
            {
                "title": "Forged identity",
                "url": (
                    "https://evil.example/not-wechat?"
                    "__biz=MzTargetBiz%3D%3D&mid=7&idx=1&sn=x"
                ),
            },
            "2026-07-24T08:00:00Z",
        )
        self.assertEqual(article.identity_status, "source_bound")
        self.assertTrue(article.article_key.startswith("url:"))
        self.assertIsNone(article.biz)
        self.assertIsNone(article.mid)

    def test_secret_query_and_unapproved_source_fail_before_network(self) -> None:
        direct_secret = self.rss_config()
        direct_secret["targets"][0]["sources"][0]["url"] = (
            "https://feeds.example/alpha.xml?token=do-not-store"
        )
        with self.assertRaisesRegex(ConfigError, "environment variable"):
            validate_config(direct_secret)

        permission_gated = self.rss_config()
        permission_gated["targets"][0]["sources"][0].update(
            permission_required=True
        )
        with self.assertRaisesRegex(ConfigError, "permission_reference"):
            validate_config(permission_gated)

    def test_cli_accepts_repeated_batch_filters(self) -> None:
        args = cli_parser().parse_args(
            [
                "run",
                "--config",
                "/tmp/targets.json",
                "--state",
                "/tmp/state.sqlite",
                "--output",
                "/tmp/output",
                "--target",
                "alpha",
                "--target",
                "beta",
                "--exclude-target",
                "beta",
                "--priority",
                "critical",
                "--priority",
                "normal",
            ]
        )
        self.assertEqual(args.target_ids, ["alpha", "beta"])
        self.assertEqual(args.exclude_target_ids, ["beta"])
        self.assertEqual(args.priorities, ["critical", "normal"])

    def test_bundled_batch_and_machineheart_assets_validate_offline(self) -> None:
        for name in (
            "targets.example.json",
            "targets.batch.example.json",
            "targets.machineheart.json",
        ):
            with self.subTest(asset=name):
                config, _ = load_config(SKILL_ROOT / "assets" / name)
                self.assertTrue(
                    any(target["enabled"] for target in config["targets"])
                )
        machineheart, _ = load_config(
            SKILL_ROOT / "assets" / "targets.machineheart.json"
        )
        sources = {
            source["id"]: source
            for source in machineheart["targets"][0]["sources"]
        }
        self.assertTrue(sources["machineheart-official-sitemap"]["enabled"])
        self.assertFalse(sources["machineheart-official-rss"]["enabled"])
        self.assertTrue(
            sources["machineheart-official-rss"]["permission_required"]
        )
        self.assertFalse(sources["xinfinite-permission-gated"]["enabled"])


class V2ParserTests(unittest.TestCase):
    def test_embedded_wechat_rss_keeps_only_verified_target_article(self) -> None:
        payload = (FIXTURES / "rss-wechat-embedded.xml").read_bytes()
        articles, rejected = parse_public_account_feed(
            payload,
            target_biz="MzA3MzI4MjgzMw==",
            expected_author="机器之心",
            timestamp_shift_hours=8,
        )
        self.assertEqual(rejected, 1)
        self.assertEqual(len(articles), 1)
        article = articles[0]
        self.assertEqual(article["title"], "机器之心的可信文章")
        self.assertEqual(article["source_author"], "机器之心")
        self.assertEqual(article["source_url"], "https://feeds.example/items/valid")
        self.assertEqual(article["original_url"], article["url"])
        self.assertEqual(article["biz"], "MzA3MzI4MjgzMw==")
        self.assertEqual(article["mid"], "2651122334")
        self.assertEqual(article["published_at"], "2026-07-24T16:00:00Z")
        self.assertIn("双重校验", article["source_summary"])

    def test_embedded_wechat_rss_rejects_unexpected_author(self) -> None:
        payload = (FIXTURES / "rss-wechat-embedded.xml").read_bytes()
        articles, rejected = parse_public_account_feed(
            payload,
            target_biz="MzA3MzI4MjgzMw==",
            expected_author="另一个公众号",
        )
        self.assertEqual(articles, [])
        self.assertEqual(rejected, 2)

    def test_rejected_embedded_entries_prevent_false_zero_result(self) -> None:
        feed_url = "https://feeds.example/machineheart.xml"
        target = {
            "id": "machineheart",
            "name": "机器之心",
            "biz": "MzA3MzI4MjgzMw==",
        }
        source = {
            "type": "rss",
            "key": "authorized-feed",
            "url": feed_url,
            "entry_link_mode": "wechat_original_from_description",
            "expected_author": "机器之心",
            "timestamp_shift_hours": 8,
            "fetch_content": False,
            "independence_group": "authorized-feed",
            "completeness": "partial",
        }
        client = FakeClient(
            {
                ("GET", feed_url): HttpResponse(
                    status=200,
                    url=feed_url,
                    headers={"content-type": "application/rss+xml"},
                    body=(FIXTURES / "rss-wechat-embedded.xml").read_bytes(),
                )
            }
        )
        fetcher = SourceFetcher(
            client,
            {
                "max_articles_per_source": 100,
                "max_sitemap_uncompressed_bytes": 4096,
            },
        )
        observation, articles = fetcher.fetch(
            target,
            source,
            now=NOW + timedelta(days=7),
            observed_at="2026-07-31T08:00:00Z",
            lookback_hours=24,
        )
        self.assertEqual(articles, [])
        self.assertEqual(observation.status, "source_unavailable")
        self.assertEqual(observation.error_code, "source_entries_rejected")
        self.assertEqual(observation.rejected_count, 1)

    def test_current_feed_entry_without_url_prevents_false_zero_result(self) -> None:
        feed_url = "https://feeds.example/malformed.xml"
        payload = (
            "<rss version='2.0'><channel><title>Malformed</title><item>"
            "<title>Current but missing URL</title>"
            "<pubDate>Fri, 24 Jul 2026 07:00:00 GMT</pubDate>"
            "</item></channel></rss>"
        )
        source = {
            "type": "rss",
            "key": "malformed-feed",
            "url": feed_url,
            "entry_link_mode": "feed",
            "timestamp_shift_hours": 0,
            "fetch_content": False,
            "independence_group": "malformed-feed",
            "completeness": "partial",
        }
        fetcher = SourceFetcher(
            FakeClient(
                {
                    ("GET", feed_url): http_response(
                        feed_url, payload, "application/rss+xml"
                    )
                }
            ),
            {
                "max_articles_per_source": 100,
                "max_sitemap_uncompressed_bytes": 4096,
            },
        )
        observation, articles = fetcher.fetch(
            {"id": "malformed", "name": "Malformed"},
            source,
            now=NOW,
            observed_at="2026-07-24T08:00:00Z",
            lookback_hours=24,
        )
        self.assertEqual(articles, [])
        self.assertEqual(observation.status, "source_unavailable")
        self.assertEqual(observation.error_code, "source_entries_rejected")
        self.assertEqual(observation.rejected_count, 1)

    def test_non_feed_xml_prevents_false_zero_result(self) -> None:
        feed_url = "https://feeds.example/maintenance.xml"
        source = {
            "type": "rss",
            "key": "maintenance-feed",
            "url": feed_url,
            "entry_link_mode": "feed",
            "timestamp_shift_hours": 0,
            "fetch_content": False,
            "independence_group": "maintenance-feed",
            "completeness": "partial",
        }
        fetcher = SourceFetcher(
            FakeClient(
                {
                    ("GET", feed_url): http_response(
                        feed_url,
                        "<html><body>maintenance</body></html>",
                        "text/html",
                    )
                }
            ),
            {
                "max_articles_per_source": 100,
                "max_sitemap_uncompressed_bytes": 4096,
            },
        )
        observation, articles = fetcher.fetch(
            {"id": "maintenance", "name": "Maintenance"},
            source,
            now=NOW,
            observed_at="2026-07-24T08:00:00Z",
            lookback_hours=24,
        )
        self.assertEqual(articles, [])
        self.assertEqual(observation.status, "source_unavailable")
        self.assertEqual(observation.error_code, "schema_changed")

    def test_json_path_and_sitemap_root_changes_prevent_false_zero(self) -> None:
        api_url = "https://provider.example/articles"
        api_source = {
            "type": "json_api",
            "key": "provider",
            "url": api_url,
            "method": "GET",
            "list_path": "items",
            "field_map": {"title": "title", "url": "url"},
            "headers_from_env": {},
            "independence_group": "provider",
            "completeness": "partial",
        }
        sitemap_url = "https://news.example/sitemap.xml"
        sitemap_source = {
            "type": "sitemap",
            "key": "sitemap",
            "url": sitemap_url,
            "url_pattern": r"^https://news\.example/articles/",
            "published_at_from_url": "",
            "nested_https_url": False,
            "fetch_content": False,
            "independence_group": "publisher",
            "completeness": "partial",
        }
        fetcher = SourceFetcher(
            FakeClient(
                {
                    ("GET", api_url): http_response(
                        api_url,
                        json.dumps({"renamed_items": []}),
                        "application/json",
                    ),
                    ("GET", sitemap_url): HttpResponse(
                        status=200,
                        url=sitemap_url,
                        headers={"content-type": "application/xml"},
                        body=b"<html><body>maintenance</body></html>",
                    ),
                }
            ),
            {
                "max_articles_per_source": 100,
                "max_sitemap_uncompressed_bytes": 4096,
            },
        )
        for source in (api_source, sitemap_source):
            with self.subTest(source=source["type"]):
                observation, articles = fetcher.fetch(
                    {"id": "changed", "name": "Changed"},
                    source,
                    now=NOW,
                    observed_at="2026-07-24T08:00:00Z",
                    lookback_hours=24,
                )
                self.assertEqual(articles, [])
                self.assertEqual(observation.status, "source_unavailable")
                self.assertEqual(observation.error_code, "schema_changed")

    def test_gzip_sitemap_recovers_nested_https_filters_and_sorts(self) -> None:
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://cdn.example/prefix/https://news.example/article/2026-07-22/older</loc></url>
          <url><loc>https://cdn.example/prefix/https://news.example/article/2026-07-24/newer</loc></url>
          <url><loc>https://cdn.example/prefix/https://other.example/article/2026-07-25/wrong-host</loc></url>
          <url><loc>https://news.example/about</loc></url>
        </urlset>"""
        articles = parse_sitemap(
            gzip.compress(xml),
            maximum_uncompressed_bytes=4096,
            url_pattern=r"^https://news\.example/article/",
            nested_https_url=True,
            published_at_from_url=r"/article/(\d{4}-\d{2}-\d{2})/",
        )
        self.assertEqual(
            [article["url"] for article in articles],
            [
                "https://news.example/article/2026-07-24/newer",
                "https://news.example/article/2026-07-22/older",
            ],
        )
        self.assertEqual(articles[0]["published_at"], "2026-07-24T00:00:00Z")

        newest_only = parse_sitemap(
            gzip.compress(xml),
            maximum_uncompressed_bytes=4096,
            url_pattern=r"^https://news\.example/article/",
            nested_https_url=True,
            published_at_from_url=r"/article/(\d{4}-\d{2}-\d{2})/",
            maximum_articles=1,
        )
        self.assertEqual(
            [article["url"] for article in newest_only],
            ["https://news.example/article/2026-07-24/newer"],
        )

    def test_sitemap_decompression_and_doctype_are_bounded(self) -> None:
        oversized = gzip.compress(b"<urlset>" + b"x" * 4096 + b"</urlset>")
        with self.assertRaisesRegex(ValueError, "byte limit"):
            parse_sitemap(
                oversized,
                maximum_uncompressed_bytes=1024,
                url_pattern=r".",
            )
        doctype = b'<!DOCTYPE x [<!ENTITY e "boom">]><urlset>&e;</urlset>'
        with self.assertRaisesRegex(ValueError, "DOCTYPE"):
            parse_sitemap(
                doctype,
                maximum_uncompressed_bytes=1024,
                url_pattern=r".",
            )

    def test_machineheart_data_service_interstitial_is_blocked(self) -> None:
        pages = (
            """
            <title>机器之心·数据服务</title>
            <h1>还在费劲爬数据？机器之心数据服务已上线</h1>
            <a>前往了解</a>
            """,
            """
            <title>文章库 | 机器之心 机器之心</title>
            <nav>文章库 PRO会员通讯 SOTA！模型 AI Shortlist 登录</nav>
            """,
        )
        for page in pages:
            with self.subTest(page=page):
                self.assertEqual(
                    classify_blocked_page(
                        page,
                        "https://www.jiqizhixin.com/articles/2026-07-24-7",
                        200,
                    ),
                    "publisher_data_service_gate",
                )


class V2BatchRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "state.sqlite"
        self.output = self.root / "output"
        self.article_html = (FIXTURES / "article-valid.html").read_text(
            encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_case(self, data: dict, client: FakeClient, **kwargs):
        config_path = self.root / "targets.json"
        config_path.write_text(json.dumps(data), encoding="utf-8")
        return run_monitor(
            config_path=str(config_path),
            state_path=str(self.state),
            output_dir=str(self.output),
            clock=lambda: NOW,
            client=client,
            **kwargs,
        )

    def test_one_failed_target_does_not_block_later_batch_targets(self) -> None:
        feed_urls = {
            "first": "https://feeds.example/first.xml",
            "failed": "https://feeds.example/failed.xml",
            "last": "https://feeds.example/last.xml",
        }
        data = {
            "version": 1,
            "defaults": {"fetch_content": False, "baseline_mode": "from_now"},
            "targets": [
                {
                    "id": target_id,
                    "name": target_id.title(),
                    "priority": "normal",
                    "sources": [{"type": "rss", "url": feed_url}],
                }
                for target_id, feed_url in feed_urls.items()
            ],
        }
        client = FakeClient(
            {
                ("GET", feed_urls["first"]): http_response(
                    feed_urls["first"],
                    rss(
                        [
                            (
                                "First update",
                                "https://news.example/first",
                                "Fri, 24 Jul 2026 07:00:00 GMT",
                            )
                        ]
                    ),
                    "application/rss+xml",
                ),
                ("GET", feed_urls["failed"]): SourceError(
                    "network_error", "offline fixture"
                ),
                ("GET", feed_urls["last"]): http_response(
                    feed_urls["last"],
                    rss(
                        [
                            (
                                "Last update",
                                "https://news.example/last",
                                "Fri, 24 Jul 2026 06:00:00 GMT",
                            )
                        ]
                    ),
                    "application/rss+xml",
                ),
            }
        )
        result = self.run_case(data, client, backfill=True)
        by_target = {
            target["target_id"]: target for target in result["report"]["targets"]
        }
        self.assertEqual(by_target["first"]["status"], "new_articles")
        self.assertEqual(by_target["failed"]["status"], "source_unavailable")
        self.assertEqual(by_target["last"]["status"], "new_articles")
        self.assertEqual(result["report"]["run_status"], "degraded")
        self.assertEqual(result["report"]["result"], "new_articles")
        self.assertEqual(result["report"]["counts"]["new"], 2)
        self.assertIn(("GET", feed_urls["last"]), client.calls)

    def test_candidate_rejection_is_counted_without_losing_valid_sibling(self) -> None:
        api_url = "https://provider.example/articles"
        data = {
            "version": 1,
            "defaults": {"fetch_content": False, "baseline_mode": "from_now"},
            "targets": [
                {
                    "id": "provider",
                    "name": "Provider",
                    "sources": [
                        {
                            "type": "json_api",
                            "url": api_url,
                            "list_path": "items",
                            "field_map": {"title": "title", "url": "url"},
                        }
                    ],
                }
            ],
        }
        payload = json.dumps(
            {
                "items": [
                    {"title": "Valid", "url": "https://news.example/valid"},
                    {"title": "Unsafe", "url": "javascript:alert(1)"},
                    {
                        "title": "Credentials",
                        "url": (
                            "https://user:password@news.example/"
                            "credential-bearing"
                        ),
                    },
                    {
                        "title": "Secret query",
                        "url": (
                            "https://news.example/secret-bearing?"
                            "token=must-not-appear"
                        ),
                    },
                ]
            }
        )
        client = FakeClient(
            {
                ("GET", api_url): http_response(
                    api_url, payload, "application/json"
                )
            }
        )
        result = self.run_case(data, client, backfill=True)
        source = result["report"]["targets"][0]["sources"][0]
        self.assertEqual(result["report"]["counts"]["candidates"], 1)
        self.assertEqual(result["report"]["counts"]["candidates_rejected"], 3)
        self.assertEqual(source["status"], "ok")
        self.assertEqual(source["rejected_count"], 3)
        self.assertNotIn("javascript:", source["error_message"] or "")
        serialized = json.dumps(result["report"])
        self.assertNotIn("password", serialized)
        self.assertNotIn("must-not-appear", serialized)

    def test_content_budget_is_critical_first_round_robin_and_per_target(self) -> None:
        target_specs = [
            ("normal", "normal"),
            ("critical-a", "critical"),
            ("critical-b", "critical"),
        ]
        routes: dict[tuple[str, str], HttpResponse | Exception] = {}
        targets = []
        article_urls: dict[str, list[str]] = {}
        for target_id, priority in target_specs:
            feed_url = f"https://feeds.example/{target_id}.xml"
            urls = [
                f"https://news.example/{target_id}/{index}" for index in range(1, 4)
            ]
            article_urls[target_id] = urls
            entries = [
                (
                    f"{target_id} article {index}",
                    article_url,
                    f"Fri, 24 Jul 2026 0{8 - index}:00:00 GMT",
                )
                for index, article_url in enumerate(urls, start=1)
            ]
            routes[("GET", feed_url)] = http_response(
                feed_url, rss(entries), "application/rss+xml"
            )
            for article_url in urls:
                routes[("GET", article_url)] = http_response(
                    article_url, self.article_html, "text/html"
                )
            targets.append(
                {
                    "id": target_id,
                    "name": target_id,
                    "priority": priority,
                    "sources": [{"type": "rss", "url": feed_url}],
                }
            )
        data = {
            "version": 1,
            "defaults": {
                "fetch_content": True,
                "baseline_mode": "from_now",
                "max_content_fetches": 5,
                "max_content_fetches_per_target": 2,
            },
            "targets": targets,
        }
        client = FakeClient(routes)
        result = self.run_case(data, client, backfill=True)
        content_calls = [
            url
            for method, url in client.calls
            if method == "GET" and url.startswith("https://news.example/")
        ]
        self.assertEqual(
            content_calls,
            [
                article_urls["critical-a"][0],
                article_urls["critical-b"][0],
                article_urls["critical-a"][1],
                article_urls["critical-b"][1],
                article_urls["normal"][0],
            ],
        )
        valid_by_target = {
            target_id: sum(
                article["target_id"] == target_id
                and article["content_status"] == "valid"
                for article in result["report"]["new_articles"]
            )
            for target_id, _ in target_specs
        }
        self.assertEqual(
            valid_by_target,
            {"normal": 1, "critical-a": 2, "critical-b": 2},
        )


class V2ReportAndSecretTests(unittest.TestCase):
    def test_mixed_healthy_and_failed_observations_are_not_no_update(self) -> None:
        observations = [
            SourceObservation(
                target_id="mixed",
                source_key="healthy",
                source_type="rss",
                independence_group="rss",
                status="observed_zero",
            ),
            SourceObservation(
                target_id="mixed",
                source_key="failed",
                source_type="sitemap",
                independence_group="sitemap",
                status="source_unavailable",
                error_code="network_error",
            ),
        ]
        report = build_report(
            run_id="mixed-run",
            started_at="2026-07-24T08:00:00Z",
            finished_at="2026-07-24T08:00:01Z",
            config_path="/tmp/targets.json",
            warnings=[],
            targets=[
                {
                    "id": "mixed",
                    "name": "Mixed",
                    "priority": "normal",
                    "enabled": True,
                }
            ],
            observations=observations,
            articles=[],
            new_articles=[],
            dry_run=True,
        )
        self.assertEqual(report["targets"][0]["status"], "partial_observation")
        self.assertEqual(report["run_status"], "degraded")
        self.assertEqual(report["result"], "coverage_incomplete")
        self.assertNotEqual(report["result"], "no_new_articles_observed")

    def test_markdown_escapes_untrusted_names_titles_and_summary_blocks(self) -> None:
        article = Article(
            target_id="safe-id",
            target_name="# Target [spoof] | > quote",
            source_type="rss",
            source_key="feed",
            independence_group="rss",
            title="# Forged heading [link] *bold*",
            url="https://news.example/safe",
            article_key="url:key",
            identity_status="source_bound",
            content_status="not_fetched",
            summary_status="limited",
            summary_basis="source_summary",
            summary="# forged\n- list\n<script>alert(1)</script>",
            summary_input="> quote\n1. fake item",
        )
        observation = SourceObservation(
            target_id="safe-id",
            source_key="feed",
            source_type="rss",
            independence_group="rss",
            status="ok",
            article_count=1,
        )
        report = build_report(
            run_id="markdown-run",
            started_at="2026-07-24T08:00:00Z",
            finished_at="2026-07-24T08:00:01Z",
            config_path="/tmp/targets.json",
            warnings=[],
            targets=[
                {
                    "id": "safe-id",
                    "name": article.target_name,
                    "priority": "normal",
                    "enabled": True,
                }
            ],
            observations=[observation],
            articles=[article],
            new_articles=[article],
            dry_run=True,
        )
        markdown = _markdown(report)
        self.assertIn(r"\# Forged heading \[link\] \*bold\*", markdown)
        self.assertIn(r"\# Target \[spoof\] \| &gt; quote", markdown)
        self.assertIn(r"\# forged", markdown)
        self.assertIn(r"\- list", markdown)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", markdown)
        self.assertNotIn("\n# forged\n", markdown)
        self.assertNotIn("\n- list\n", markdown)

    def test_markdown_percent_encodes_article_link_delimiters(self) -> None:
        article = Article(
            target_id="safe-id",
            target_name="Safe",
            source_type="rss",
            source_key="feed",
            independence_group="rss",
            title="Safe title",
            url=(
                "https://news.example>/path)"
                "[Injected](https://evil.example)"
            ),
            article_key="url:key",
            identity_status="source_bound",
        )
        report = build_report(
            run_id="markdown-url-run",
            started_at="2026-07-24T08:00:00Z",
            finished_at="2026-07-24T08:00:01Z",
            config_path="/tmp/targets.json",
            warnings=[],
            targets=[
                {
                    "id": "safe-id",
                    "name": "Safe",
                    "priority": "normal",
                    "enabled": True,
                }
            ],
            observations=[
                SourceObservation(
                    target_id="safe-id",
                    source_key="feed",
                    source_type="rss",
                    independence_group="rss",
                    status="ok",
                    article_count=1,
                )
            ],
            articles=[article],
            new_articles=[article],
            dry_run=True,
        )
        markdown = _markdown(report)
        self.assertIn("news.example%3E/path)%5BInjected%5D", markdown)
        self.assertNotIn("news.example>/", markdown)
        self.assertNotIn(">)[Injected](", markdown)

    def test_secret_query_is_redacted_from_http_error(self) -> None:
        secret = "super-secret-feed-token"
        client = HttpClient(
            timeout_seconds=1,
            max_response_bytes=1024,
            retry_count=0,
        )
        error = HTTPError(
            f"https://feeds.example/rss?token={secret}",
            500,
            "Server Error",
            {},
            None,
        )
        opener = MagicMock()
        opener.open.side_effect = error
        with patch(
            "pola_wechat_reader.http_client.build_opener", return_value=opener
        ):
            with self.assertRaises(SourceError) as raised:
                client.request(
                    "GET",
                    f"https://feeds.example/rss?token={secret}",
                    sensitive_url=True,
                )
        self.assertEqual(raised.exception.code, "http_error")
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn("token=", str(raised.exception))
        self.assertIn("https://feeds.example/rss", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)


if __name__ == "__main__":
    unittest.main()
