#!/usr/bin/env python3
"""Run deterministic and optional live validation for the Skill.

Module: validation harness
Purpose: prove parser/state semantics offline and probe public endpoints explicitly
Created: 2026-07-24
Author: Codex
Dependencies: Python 3.10+ standard library
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from pola_wechat_reader.config import DEFAULTS, load_config  # noqa: E402
from pola_wechat_reader.http_client import HttpClient  # noqa: E402
from pola_wechat_reader.identity import (  # noqa: E402
    canonicalize_url,
    extract_wechat_identity,
)
from pola_wechat_reader.sources import SourceFetcher  # noqa: E402


def offline() -> bool:
    suite = unittest.defaultTestLoader.discover(str(SKILL_ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return result.wasSuccessful()


def live() -> tuple[bool, dict]:
    defaults = {
        **DEFAULTS,
        "lookback_hours": 24 * 365 * 20,
        "max_pages": 1,
        "fetch_content": True,
        "max_content_fetches": 1,
    }
    client = HttpClient(
        timeout_seconds=20,
        max_response_bytes=5 * 1024 * 1024,
        retry_count=0,
    )
    fetcher = SourceFetcher(client, defaults)
    cases = [
        (
            {
                "id": "album-live",
                "name": "Album Live",
                "biz": "MzI0NTU3NTc5Ng==",
            },
            {
                "type": "wechat_album",
                "key": "album-live",
                "album_id": "4482506796406177793",
                "independence_group": "wechat_public",
                "completeness": "partial",
            },
        ),
        (
            {
                "id": "homepage-live",
                "name": "Homepage Live",
                "biz": "MzA3MDM3NjE5NQ==",
            },
            {
                "type": "wechat_homepage",
                "key": "homepage-live",
                "hid": "16",
                "independence_group": "wechat_public",
                "completeness": "partial",
            },
        ),
        (
            {
                "id": "machineheart-live",
                "name": "机器之心",
                "biz": "MzA3MzI4MjgzMw==",
            },
            {
                "type": "sitemap",
                "key": "machineheart-official-sitemap-live",
                "url": "https://www.jiqizhixin.com/shared/sitemap.xml.gz",
                "url_pattern": (
                    r"^https://(?:www\.)?jiqizhixin\.com/articles/"
                    r"\d{4}-\d{2}-\d{2}-[A-Za-z0-9._~-]+/?"
                    r"(?:[?#].*)?$"
                ),
                "published_at_from_url": (
                    r"/articles/(\d{4}-\d{2}-\d{2})-"
                ),
                "nested_https_url": True,
                "max_articles": 20,
                "fetch_content": False,
                "independence_group": "machineheart_official",
                "completeness": "partial",
            },
        ),
    ]
    now = datetime.now(timezone.utc)
    results = []
    healthy = True
    first_article = None
    first_biz = None
    for target, source in cases:
        observation, articles = fetcher.fetch(
            target,
            source,
            now=now,
            observed_at=now.isoformat(),
            lookback_hours=24 * 365 * 20,
        )
        if source["type"] == "sitemap":
            identities_ok = bool(articles) and all(
                item["url"].startswith("https://www.jiqizhixin.com/articles/")
                and bool(item.get("published_at"))
                for item in articles
            )
        else:
            identities_ok = bool(articles) and all(
                extract_wechat_identity(item["url"]).get("__biz")
                == target["biz"]
                for item in articles
            )
        case_ok = observation.status == "ok" and identities_ok
        healthy = healthy and case_ok
        results.append(
            {
                "source": source["type"],
                "status": observation.status,
                "articles": len(articles),
                "identity_ok": identities_ok,
                "error": observation.error_code,
            }
        )
        if first_article is None and articles:
            first_article = {
                **articles[0],
                "url": canonicalize_url(articles[0]["url"]),
            }
            first_biz = target["biz"]

    content_result = {"status": "not_tested"}
    if first_article:
        content_result = fetcher.fetch_content(
            first_article,
            target_biz=first_biz,
            text_limit=defaults["article_text_limit"],
        )
        if content_result["status"] not in {"valid", "blocked"}:
            healthy = False
    payload = {
        "live_status": "pass" if healthy else "degraded",
        "sources": results,
        "article_content": {
            "status": content_result.get("status"),
            "reason": content_result.get("reason"),
        },
    }
    return healthy, payload


def live_private() -> tuple[bool, dict]:
    """Probe the explicitly enabled private Machineheart RSS paths."""
    config, warnings = load_config(
        SKILL_ROOT / "assets" / "targets.machineheart.private.json"
    )
    target = config["targets"][0]
    defaults = {
        **config["defaults"],
        "max_pages": 1,
        "max_articles_per_source": 20,
        "max_content_fetches": 1,
    }
    client = HttpClient(
        timeout_seconds=20,
        max_response_bytes=5 * 1024 * 1024,
        retry_count=0,
    )
    fetcher = SourceFetcher(client, defaults)
    now = datetime.now(timezone.utc)
    results = []
    healthy = True
    first_article = None
    for configured_source in target["sources"]:
        if not configured_source["enabled"] or configured_source["type"] != "rss":
            continue
        source = {**configured_source, "max_articles": 20}
        observation, articles = fetcher.fetch(
            target,
            source,
            now=now,
            observed_at=now.isoformat(),
            lookback_hours=24 * 30,
        )
        if source["id"] == "machineheart-official-rss":
            token_configured = bool(os.environ.get("MACHINEHEART_RSS_TOKEN"))
            case_ok = (
                observation.status in {"ok", "observed_zero"}
                if token_configured
                else observation.error_code == "missing_secret"
            )
        else:
            identity_ok = bool(articles) and all(
                extract_wechat_identity(item["url"]).get("__biz")
                == target["biz"]
                for item in articles
            )
            case_ok = observation.status == "ok" and identity_ok
            if first_article is None and articles:
                first_article = {
                    **articles[0],
                    "url": canonicalize_url(articles[0]["url"]),
                }
        healthy = healthy and case_ok
        results.append(
            {
                "source": source["id"],
                "status": observation.status,
                "articles": len(articles),
                "error": observation.error_code,
                "expected_without_token": (
                    "missing_secret"
                    if source["id"] == "machineheart-official-rss"
                    and not os.environ.get("MACHINEHEART_RSS_TOKEN")
                    else None
                ),
            }
        )

    content_result = {"status": "not_tested", "reason": None}
    if first_article:
        content_result = fetcher.fetch_content(
            first_article,
            target_biz=target["biz"],
            text_limit=defaults["article_text_limit"],
        )
        if content_result["status"] not in {"valid", "blocked"}:
            healthy = False
    return healthy, {
        "live_private_status": "pass" if healthy else "degraded",
        "source_permission_policy": config["defaults"][
            "source_permission_policy"
        ],
        "warnings": warnings,
        "sources": results,
        "article_content": {
            "status": content_result.get("status"),
            "reason": content_result.get("reason"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the WeChat reader Skill")
    live_mode = parser.add_mutually_exclusive_group()
    live_mode.add_argument(
        "--live",
        action="store_true",
        help="also probe default public endpoints",
    )
    live_mode.add_argument(
        "--live-private",
        action="store_true",
        help="probe private-mode Machineheart RSS paths",
    )
    parser.add_argument(
        "--allow-degraded",
        action="store_true",
        help="return 0 when offline tests pass but live sources are degraded",
    )
    args = parser.parse_args(argv)
    if not offline():
        return 1
    if not args.live and not args.live_private:
        print("OFFLINE_HARNESS_OK")
        return 0
    try:
        healthy, payload = live_private() if args.live_private else live()
    except Exception as exc:
        healthy = False
        payload = {
            (
                "live_private_status"
                if args.live_private
                else "live_status"
            ): "degraded",
            "error": f"{type(exc).__name__}: {exc}",
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if healthy or args.allow_degraded:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
