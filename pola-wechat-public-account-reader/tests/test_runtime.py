"""Runtime, state and report tests for the public-account reader.

Module: offline integration harness
Purpose: verify status truthfulness, deduplication, transactions and run locking
Created: 2026-07-24
Author: Codex
Dependencies: Python standard library
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from pola_wechat_reader.http_client import HttpResponse  # noqa: E402
from pola_wechat_reader.models import RunLocked, SourceError, SourceObservation  # noqa: E402
from pola_wechat_reader.runner import run_monitor  # noqa: E402
from pola_wechat_reader.storage import RunLock, StateStore  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)

class FakeClient:
    def __init__(self, responses: dict[tuple[str, str], HttpResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs) -> HttpResponse:
        key = (method.upper(), url)
        self.calls.append(key)
        response = self.responses.get(key)
        if response is None:
            raise SourceError("unexpected_request", f"no fixture for {key}")
        if isinstance(response, Exception):
            raise response
        return response


def response(url: str, body: str, content_type: str) -> HttpResponse:
    return HttpResponse(
        status=200,
        url=url,
        headers={"content-type": content_type},
        body=body.encode("utf-8"),
    )


def config(feed_urls: list[str], *, fetch_content: bool = True) -> dict:
    return {
        "version": 1,
        "defaults": {
            "fetch_content": fetch_content,
            "baseline_mode": "from_now",
        },
        "targets": [
            {
                "id": "fixture",
                "name": "Fixture Publisher",
                "priority": "normal",
                "sources": [
                    {
                        "type": "rss",
                        "url": url,
                        "independence_group": f"group-{index}",
                    }
                    for index, url in enumerate(feed_urls)
                ],
            }
        ],
    }


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "state.sqlite"
        self.output = self.root / "output"
        self.feed_url = "https://feeds.example/main.xml"
        self.article_url = "https://news.example/articles/one"
        self.feed = (FIXTURES / "rss.xml").read_text(encoding="utf-8")
        self.article = (FIXTURES / "article-valid.html").read_text(encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_config(self, data: dict) -> Path:
        path = self.root / "targets.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def client(self, feed_urls: list[str] | None = None) -> FakeClient:
        urls = feed_urls or [self.feed_url]
        routes: dict[tuple[str, str], HttpResponse | Exception] = {
            ("GET", url): response(url, self.feed, "application/rss+xml")
            for url in urls
        }
        routes[("GET", self.article_url)] = response(
            self.article_url, self.article, "text/html; charset=utf-8"
        )
        return FakeClient(routes)

    def run_case(self, data: dict, client: FakeClient, **kwargs):
        path = self.write_config(data)
        return run_monitor(
            config_path=str(path),
            state_path=str(self.state),
            output_dir=str(self.output),
            clock=lambda: NOW,
            client=client,
            **kwargs,
        )

    def test_backfill_then_second_run_deduplicates(self) -> None:
        data = config([self.feed_url])
        first = self.run_case(data, self.client(), backfill=True)
        self.assertEqual(first["report"]["counts"]["new"], 1)
        self.assertEqual(first["report"]["new_articles"][0]["content_status"], "valid")
        self.assertEqual(first["report"]["new_articles"][0]["summary_basis"], "full_text")
        self.assertTrue(first["report"]["new_articles"][0]["summary"])
        second = self.run_case(data, self.client(), backfill=True)
        self.assertEqual(second["report"]["counts"]["new"], 0)
        self.assertEqual(second["report"]["targets"][0]["status"], "no_new_articles_observed")

    def test_from_now_first_run_builds_baseline_without_alert(self) -> None:
        result = self.run_case(config([self.feed_url]), self.client())
        self.assertEqual(result["report"]["counts"]["new"], 0)
        store = StateStore(self.state)
        try:
            self.assertTrue(store.target_has_articles("fixture"))
        finally:
            store.close()

    def test_two_independent_equal_sources_are_high_confidence(self) -> None:
        urls = [self.feed_url, "https://feeds.example/second.xml"]
        result = self.run_case(
            config(urls, fetch_content=False), self.client(urls), backfill=True
        )
        self.assertEqual(result["report"]["counts"]["new"], 1)
        self.assertEqual(result["report"]["targets"][0]["coverage"], "high_confidence")
        self.assertEqual(len(result["report"]["new_articles"][0]["discovered_by"]), 2)

    def test_partial_empty_plus_failure_is_not_no_update(self) -> None:
        empty_url = "https://feeds.example/empty.xml"
        fail_url = "https://feeds.example/fail.xml"
        empty_feed = "<rss version='2.0'><channel><title>Empty</title></channel></rss>"
        client = FakeClient(
            {
                ("GET", empty_url): response(empty_url, empty_feed, "application/rss+xml"),
                ("GET", fail_url): SourceError("network_error", "fixture failure"),
            }
        )
        result = self.run_case(
            config([empty_url, fail_url], fetch_content=False), client
        )
        target = result["report"]["targets"][0]
        self.assertEqual(target["status"], "partial_observation")
        self.assertEqual(target["coverage"], "partial")
        self.assertEqual(result["report"]["run_status"], "degraded")
        self.assertEqual(result["report"]["result"], "coverage_incomplete")

    def test_all_sources_failed_means_unknown_and_exit_one(self) -> None:
        client = FakeClient(
            {("GET", self.feed_url): SourceError("network_error", "offline")}
        )
        result = self.run_case(config([self.feed_url], fetch_content=False), client)
        self.assertEqual(result["report"]["coverage"], "unknown")
        self.assertEqual(result["exit_code"], 1)

    def test_captcha_keeps_metadata_but_not_captcha_summary(self) -> None:
        captcha = (FIXTURES / "article-captcha.html").read_text(encoding="utf-8")
        client = self.client()
        client.responses[("GET", self.article_url)] = response(
            "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha",
            captcha,
            "text/html",
        )
        result = self.run_case(config([self.feed_url]), client, backfill=True)
        article = result["report"]["new_articles"][0]
        self.assertEqual(article["content_status"], "blocked")
        self.assertNotIn("环境异常", article["summary_input"])
        self.assertIn("RSS", article["summary_input"])
        self.assertEqual(article["summary_basis"], "source_summary")

    def test_captcha_text_in_source_summary_is_suppressed(self) -> None:
        captcha = (FIXTURES / "article-captcha.html").read_text(encoding="utf-8")
        api_url = "https://provider.example/articles"
        payload = json.dumps(
            {
                "data": {
                    "items": [
                        {
                            "title": "Provider Article",
                            "url": self.article_url,
                            "published_at": "2026-07-23T08:00:00Z",
                            "digest": "当前环境异常，完成验证后即可继续访问。",
                        }
                    ]
                }
            }
        )
        data = {
            "version": 1,
            "defaults": {"baseline_mode": "from_now"},
            "targets": [
                {
                    "id": "provider",
                    "name": "Provider",
                    "sources": [
                        {
                            "type": "json_api",
                            "url": api_url,
                            "field_map": {
                                "title": "title",
                                "url": "url",
                                "published_at": "published_at",
                                "summary": "digest",
                            },
                            "list_path": "data.items",
                        }
                    ],
                }
            ],
        }
        client = FakeClient(
            {
                ("GET", api_url): response(api_url, payload, "application/json"),
                ("GET", self.article_url): response(
                    "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha",
                    captcha,
                    "text/html",
                ),
            }
        )
        result = self.run_case(data, client, backfill=True)
        article = result["report"]["new_articles"][0]
        self.assertEqual(article["content_status"], "blocked")
        self.assertEqual(article["summary"], "")
        self.assertEqual(article["summary_input"], "")
        self.assertEqual(article["source_summary"], "")
        self.assertEqual(article["summary_basis"], "none")

    def test_captcha_word_in_legitimate_url_does_not_erase_summary(self) -> None:
        article_url = "https://news.example/research/captcha-security"
        feed = self.feed.replace(
            "https://news.example/articles/one?utm_source=test",
            article_url,
        )
        client = FakeClient(
            {
                ("GET", self.feed_url): response(
                    self.feed_url, feed, "application/rss+xml"
                )
            }
        )
        result = self.run_case(
            config([self.feed_url], fetch_content=False),
            client,
            backfill=True,
        )
        article = result["report"]["new_articles"][0]
        self.assertEqual(article["url"], article_url)
        self.assertEqual(article["summary_basis"], "source_summary")
        self.assertIn("RSS", article["summary"])

    def test_same_article_is_scoped_independently_per_target(self) -> None:
        data = {
            "version": 1,
            "defaults": {"fetch_content": False, "baseline_mode": "from_now"},
            "targets": [
                {
                    "id": target_id,
                    "name": target_id,
                    "sources": [{"type": "rss", "url": self.feed_url}],
                }
                for target_id in ("first", "second")
            ],
        }
        first = self.run_case(data, self.client(), backfill=True)
        self.assertEqual(first["report"]["counts"]["new"], 2)
        self.assertEqual(
            {item["target_id"] for item in first["report"]["new_articles"]},
            {"first", "second"},
        )
        store = StateStore(self.state)
        try:
            count = store.connection.execute(
                "SELECT COUNT(*) FROM articles"
            ).fetchone()[0]
            self.assertEqual(count, 2)
        finally:
            store.close()
        second = self.run_case(data, self.client(), backfill=True)
        self.assertEqual(second["report"]["counts"]["new"], 0)

    def test_candidate_limit_stops_remaining_sources(self) -> None:
        second_url = "https://feeds.example/second.xml"
        data = config([self.feed_url, second_url], fetch_content=False)
        data["defaults"]["max_total_candidates"] = 1
        result = self.run_case(
            data,
            self.client([self.feed_url, second_url]),
            backfill=True,
        )
        self.assertEqual(result["report"]["counts"]["candidates"], 1)
        self.assertEqual(result["report"]["counts"]["source_failures"], 1)
        self.assertEqual(
            result["report"]["errors"][0]["code"], "candidate_limit_exceeded"
        )

    def test_database_run_history_is_pruned_transactionally(self) -> None:
        store = StateStore(self.state)
        observation = SourceObservation(
            target_id="fixture",
            source_key="rss",
            source_type="rss",
            independence_group="rss",
            status="observed_zero",
            observed_at="2026-01-01T00:00:00Z",
        )
        try:
            store.begin()
            for run_id, started_at in (
                ("old", "2026-01-01T00:00:00Z"),
                ("new", "2026-07-24T00:00:00Z"),
            ):
                store.record_run_start(run_id, started_at)
                store.record_observation(run_id, observation)
                store.finish_run(
                    run_id,
                    finished_at=started_at,
                    status="success",
                    coverage="partial",
                    report_json="{}",
                )
            store.prune_history("2026-07-01T00:00:00Z")
            store.commit()
            runs = store.connection.execute(
                "SELECT run_id FROM runs ORDER BY run_id"
            ).fetchall()
            observations = store.connection.execute(
                "SELECT run_id FROM source_observations"
            ).fetchall()
            self.assertEqual([row[0] for row in runs], ["new"])
            self.assertEqual([row[0] for row in observations], ["new"])
        finally:
            store.close()

    def test_transaction_rollback_does_not_keep_article(self) -> None:
        store = StateStore(self.state)
        try:
            store.begin()
            store.record_run_start("rollback", "2026-07-24T00:00:00Z")
            store.rollback()
            row = store.connection.execute(
                "SELECT 1 FROM runs WHERE run_id='rollback'"
            ).fetchone()
            self.assertIsNone(row)
        finally:
            store.close()

    def test_run_lock_rejects_overlap(self) -> None:
        with RunLock(self.state):
            with self.assertRaises(RunLocked):
                with RunLock(self.state):
                    pass


if __name__ == "__main__":
    unittest.main()
