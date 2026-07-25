"""Parser and configuration tests for the public-account reader.

Module: offline parser harness
Purpose: guard public payload shapes, identity and CAPTCHA semantics
Created: 2026-07-24
Author: Codex
Dependencies: Python standard library
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from pola_wechat_reader.config import load_config, validate_config  # noqa: E402
from pola_wechat_reader.identity import (  # noqa: E402
    article_key,
    canonicalize_url,
    extract_wechat_identity,
)
from pola_wechat_reader.models import ConfigError  # noqa: E402
from pola_wechat_reader.parsers import (  # noqa: E402
    classify_blocked_page,
    parse_album_payload,
    parse_article_html,
    parse_feed,
    parse_homepage_payload,
    parse_json_api,
)


FIXTURES = Path(__file__).parent / "fixtures"
BIZ = "MzI2MTcxMjI0MQ=="


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ConfigTests(unittest.TestCase):
    def base(self) -> dict:
        return {
            "version": 1,
            "targets": [
                {
                    "id": "fixture",
                    "name": "Fixture",
                    "biz": BIZ,
                    "sources": [{"type": "wechat_album", "album_id": "123"}],
                }
            ],
        }

    def test_valid_config_is_normalized(self) -> None:
        config, warnings = validate_config(self.base())
        self.assertEqual(config["defaults"]["baseline_mode"], "from_now")
        self.assertEqual(config["defaults"]["max_run_seconds"], 600)
        self.assertEqual(config["defaults"]["max_total_candidates"], 5000)
        self.assertEqual(
            config["targets"][0]["sources"][0]["completeness"], "partial"
        )
        self.assertTrue(warnings)

    def test_rejects_duplicate_target_and_biz(self) -> None:
        data = self.base()
        data["targets"].append({**data["targets"][0]})
        with self.assertRaises(ConfigError):
            validate_config(data)

    def test_rejects_missing_album_id_and_complete_free_source(self) -> None:
        data = self.base()
        data["targets"][0]["sources"] = [{"type": "wechat_album"}]
        with self.assertRaises(ConfigError):
            validate_config(data)
        data = self.base()
        data["targets"][0]["sources"][0]["completeness"] = "complete"
        with self.assertRaises(ConfigError):
            validate_config(data)

    def test_rejects_private_and_credential_urls(self) -> None:
        for url in ("https://127.0.0.1/feed", "https://u:p@example.com/feed"):
            data = self.base()
            data["targets"][0].pop("biz")
            data["targets"][0]["sources"] = [{"type": "rss", "url": url}]
            with self.assertRaises(ConfigError):
                validate_config(data)

    def test_rejects_cookie_header_even_from_environment(self) -> None:
        data = self.base()
        data["targets"][0]["sources"] = [
            {
                "type": "json_api",
                "url": "https://provider.example/articles",
                "field_map": {"title": "title", "url": "url"},
                "headers_from_env": {
                    "Cookie": {"env": "WECHAT_COOKIE"}
                },
            }
        ]
        with self.assertRaises(ConfigError):
            validate_config(data)
        data = self.base()
        data["targets"][0]["sources"] = [
            {
                "type": "json_api",
                "url": "https://provider.example/articles",
                "field_map": {"title": "title", "url": "url"},
                "headers_from_env": {
                    "Authorization": {
                        "env": "WECHAT_PROVIDER_KEY",
                        "prefix": "Bearer \n",
                    }
                },
            }
        ]
        with self.assertRaises(ConfigError):
            validate_config(data)

    def test_rejects_unbounded_target_and_source_cardinality(self) -> None:
        data = self.base()
        data["targets"][0]["sources"] *= 11
        with self.assertRaises(ConfigError):
            validate_config(data)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO support required")
    def test_config_loader_rejects_non_regular_file_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.fifo"
            os.mkfifo(path)
            with self.assertRaises(ConfigError):
                load_config(path)
        data = self.base()
        data["targets"] *= 101
        with self.assertRaises(ConfigError):
            validate_config(data)


class PublicPayloadTests(unittest.TestCase):
    def test_album_array_cursor_and_identity(self) -> None:
        articles, has_more, cursor = parse_album_payload(fixture("album-page.json"), BIZ)
        self.assertTrue(has_more)
        self.assertEqual(cursor, {"begin_msgid": "1001", "begin_itemidx": "1"})
        self.assertEqual(articles[0]["biz"], BIZ)

    def test_album_accepts_object_and_null(self) -> None:
        data = json.loads(fixture("album-page.json"))
        data["getalbum_resp"]["article_list"] = data["getalbum_resp"]["article_list"][0]
        self.assertEqual(len(parse_album_payload(data, BIZ)[0]), 1)
        data["getalbum_resp"]["article_list"] = None
        data["getalbum_resp"]["continue_flag"] = "0"
        self.assertEqual(parse_album_payload(data, BIZ)[0], [])

    def test_album_rejects_ret_schema_and_wrong_biz(self) -> None:
        for data in (
            {"base_resp": {"ret": 10004}},
            {"getalbum_resp": {"article_list": []}},
            {"base_resp": {"ret": 0}, "getalbum_resp": {}},
        ):
            with self.assertRaises(ValueError):
                parse_album_payload(data, BIZ)
        data = json.loads(fixture("album-page.json"))
        data["getalbum_resp"]["article_list"][0]["url"] = (
            "https://mp.weixin.qq.com/s?__biz=WRONG&mid=1001&idx=1&sn=abc"
        )
        with self.assertRaises(ValueError):
            parse_album_payload(data, BIZ)

    def test_homepage_and_empty_page(self) -> None:
        articles, has_more = parse_homepage_payload(
            fixture("homepage-page.json"), BIZ
        )
        self.assertFalse(has_more)
        self.assertEqual(articles[0]["biz"], BIZ)
        empty = {"base_resp": {"ret": 0}, "appmsg_list": [], "has_more": 0}
        self.assertEqual(parse_homepage_payload(empty, BIZ)[0], [])
        with self.assertRaises(ValueError):
            parse_homepage_payload({"base_resp": {"ret": 0}}, BIZ)

    def test_rss_atom_and_doctype(self) -> None:
        rss = parse_feed(fixture("rss.xml"))
        self.assertEqual(rss[0]["title"], "Fixture Media Article")
        atom = """
        <feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <title>Atom One</title>
          <link rel="alternate" href="https://news.example/atom"/>
          <updated>2026-07-23T08:00:00Z</updated><summary>Atom summary</summary>
        </entry></feed>
        """
        self.assertEqual(parse_feed(atom)[0]["url"], "https://news.example/atom")
        with self.assertRaises(ValueError):
            parse_feed('<!DOCTYPE rss [<!ENTITY x "bad">]><rss>&x;</rss>')

    def test_json_api_mapping(self) -> None:
        articles = parse_json_api(
            {"data": {"items": [{"headline": "One", "href": "https://news.example/1"}]}},
            "data.items",
            {"title": "headline", "url": "href"},
        )
        self.assertEqual(articles[0]["title"], "One")


class IdentityAndContentTests(unittest.TestCase):
    def test_wechat_url_decoding_and_canonical_dedup(self) -> None:
        first = (
            "https://mp.weixin.qq.com/s?mid=1001&__biz="
            "MzI2MTcxMjI0MQ%3D%3D&idx=1&sn=abc&scene=19#rd"
        )
        second = (
            "http://mp.weixin.qq.com/s?__biz=MzI2MTcxMjI0MQ=="
            "&mid=1001&idx=1&sn=abc"
        )
        self.assertEqual(extract_wechat_identity(first)["__biz"], BIZ)
        self.assertEqual(canonicalize_url(first), canonicalize_url(second))
        self.assertEqual(
            article_key({"url": first, **extract_wechat_identity(first)}, BIZ),
            article_key({"url": second, **extract_wechat_identity(second)}, BIZ),
        )

    def test_long_captcha_is_blocked(self) -> None:
        html = fixture("article-captcha.html") + ("填充文本" * 500)
        reason = classify_blocked_page(
            html,
            "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?token=x",
            200,
        )
        self.assertEqual(reason, "captcha")
        parsed = parse_article_html(html, "https://mp.weixin.qq.com/s?__biz=x")
        self.assertEqual(parsed["content_status"], "blocked")
        self.assertEqual(parsed["content_text"], "")

    def test_valid_article_text_is_extracted(self) -> None:
        parsed = parse_article_html(
            fixture("article-valid.html"), "https://news.example/articles/one"
        )
        self.assertEqual(parsed["content_status"], "valid")
        self.assertGreater(len(parsed["content_text"]), 120)


if __name__ == "__main__":
    unittest.main()
