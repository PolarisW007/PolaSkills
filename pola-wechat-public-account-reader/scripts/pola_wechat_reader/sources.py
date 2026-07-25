"""Public discovery adapters and bounded article retrieval.

Module: source adapters
Purpose: normalize Album, Homepage, RSS, article URL and JSON API observations
Created: 2026-07-24
Author: Codex
Dependencies: Python standard library and local parser/http modules
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .http_client import HttpClient
from .identity import canonicalize_url, clean_text, extract_wechat_identity, parse_datetime
from .models import SourceError, SourceObservation
from .parsers import (
    classify_blocked_page,
    parse_album_payload,
    parse_article_html,
    parse_feed,
    parse_public_account_feed,
    parse_homepage_payload,
    parse_json_api,
    parse_sitemap,
)


WX_HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "X-Requested-With": "XMLHttpRequest",
}


def _json_payload(text: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SourceError("invalid_json", f"{label} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise SourceError("schema_changed", f"{label} response is not an object")
    return payload


def _window_filter(
    articles: list[dict[str, Any]],
    *,
    now: datetime,
    lookback_hours: int,
    limit: int,
    max_future_hours: int = 24,
) -> tuple[list[dict[str, Any]], int]:
    since = now - timedelta(hours=lookback_hours)
    future_limit = now + timedelta(hours=max_future_hours)
    accepted: list[dict[str, Any]] = []
    rejected = 0
    for article in articles:
        published = parse_datetime(article.get("published_at"))
        if published:
            if published < since:
                continue
            if published > future_limit:
                rejected += 1
                continue
        url = str(article.get("url") or "").strip()
        if not url:
            rejected += 1
            continue
        article["url"] = url
        accepted.append(article)
        if len(accepted) >= limit:
            break
    return accepted, rejected


class SourceFetcher:
    """Dispatch public sources through one bounded transport."""

    def __init__(self, client: HttpClient, defaults: dict[str, Any]) -> None:
        self.client = client
        self.defaults = defaults

    def fetch(
        self,
        target: dict[str, Any],
        source: dict[str, Any],
        *,
        now: datetime,
        observed_at: str,
        lookback_hours: int,
    ) -> tuple[SourceObservation, list[dict[str, Any]]]:
        try:
            articles, pages, rejected = self._dispatch(target, source)
            article_limit = source.get(
                "max_articles", self.defaults["max_articles_per_source"]
            )
            filtered, window_rejected = _window_filter(
                articles,
                now=now,
                lookback_hours=lookback_hours,
                limit=article_limit,
                max_future_hours=int(source.get("max_future_hours", 24)),
            )
            rejected += window_rejected
            status = "ok" if filtered else "observed_zero"
            error_code = None
            error_message = None
            if rejected:
                error_message = (
                    f"{rejected} source entr"
                    f"{'y was' if rejected == 1 else 'ies were'} rejected "
                    "during source validation or time-window filtering"
                )
                if not filtered:
                    status = "source_unavailable"
                    error_code = "source_entries_rejected"
            observation = SourceObservation(
                target_id=target["id"],
                source_key=source["key"],
                source_type=source["type"],
                independence_group=source["independence_group"],
                status=status,
                coverage=source["completeness"],
                observed_at=observed_at,
                article_count=len(filtered),
                pages_fetched=pages,
                rejected_count=rejected,
                error_code=error_code,
                error_message=error_message,
                truncated=len(articles) >= article_limit
                and len(filtered) >= article_limit,
                stop_reason=(
                    "source_article_limit"
                    if len(articles) >= article_limit
                    and len(filtered) >= article_limit
                    else None
                ),
            )
            return observation, filtered
        except SourceError as exc:
            return (
                SourceObservation(
                    target_id=target["id"],
                    source_key=source["key"],
                    source_type=source["type"],
                    independence_group=source["independence_group"],
                    status=exc.status,
                    coverage="partial",
                    observed_at=observed_at,
                    error_code=exc.code,
                    error_message=str(exc)[:500],
                    http_status=exc.http_status,
                ),
                [],
            )
        except (ValueError, KeyError, TypeError) as exc:
            return (
                SourceObservation(
                    target_id=target["id"],
                    source_key=source["key"],
                    source_type=source["type"],
                    independence_group=source["independence_group"],
                    status="source_unavailable",
                    coverage="partial",
                    observed_at=observed_at,
                    error_code="schema_changed",
                    error_message=str(exc)[:500],
                ),
                [],
            )

    def _dispatch(
        self, target: dict[str, Any], source: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], int, int]:
        source_type = source["type"]
        if source_type == "wechat_album":
            return self._album(target, source)
        if source_type == "wechat_homepage":
            return self._homepage(target, source)
        if source_type == "rss":
            return self._rss(target, source)
        if source_type == "sitemap":
            return self._sitemap(source)
        if source_type == "article_url":
            identity = extract_wechat_identity(source["url"])
            return (
                [
                    {
                        "title": source.get("title", "Known article URL"),
                        "url": source["url"],
                        "published_at": source.get("published_at"),
                        "source_summary": source.get("summary", ""),
                        "biz": identity.get("__biz"),
                        "mid": identity.get("mid"),
                        "idx": identity.get("idx"),
                        "sn": identity.get("sn"),
                    }
                ],
                1,
                0,
            )
        if source_type == "json_api":
            return self._json_api(source)
        raise SourceError("unsupported_source", f"unsupported source {source_type}")

    def _page_limit(self, source: dict[str, Any]) -> int:
        return int(source.get("max_pages", self.defaults["max_pages"]))

    def _album(
        self, target: dict[str, Any], source: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], int, int]:
        base = "https://mp.weixin.qq.com/mp/appmsgalbum"
        cursor: dict[str, str] | None = None
        seen_cursors: set[tuple[str, str]] = set()
        articles: list[dict[str, Any]] = []
        pages = 0
        for _ in range(self._page_limit(source)):
            query = {
                "action": "getalbum",
                "__biz": target["biz"],
                "album_id": source["album_id"],
                "count": "20",
                "f": "json",
                "is_reverse": source.get("is_reverse", "0"),
            }
            if cursor:
                query.update(cursor)
            response = self.client.request("GET", f"{base}?{urlencode(query)}", headers=WX_HEADERS)
            text = response.text()
            blocked = classify_blocked_page(text, response.url, response.status)
            if blocked:
                raise SourceError(blocked, f"Album blocked: {blocked}", status="blocked")
            page, has_more, next_cursor = parse_album_payload(
                _json_payload(text, "Album"), target["biz"]
            )
            pages += 1
            articles.extend(page)
            if not has_more:
                break
            if not next_cursor:
                raise SourceError("pagination_stalled", "Album has_more without cursor")
            cursor_key = (
                next_cursor.get("begin_msgid", ""),
                next_cursor.get("begin_itemidx", ""),
            )
            if cursor_key in seen_cursors:
                raise SourceError("pagination_loop", "Album cursor repeated")
            seen_cursors.add(cursor_key)
            cursor = next_cursor
        return articles, pages, 0

    def _homepage(
        self, target: dict[str, Any], source: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], int, int]:
        base = "https://mp.weixin.qq.com/mp/homepage"
        begin = 0
        articles: list[dict[str, Any]] = []
        pages = 0
        for _ in range(self._page_limit(source)):
            query = {
                "__biz": target["biz"],
                "hid": source["hid"],
                "begin": str(begin),
                "count": "20",
                "action": "appmsg_list",
            }
            if source.get("cid") is not None:
                query["cid"] = str(source["cid"])
            response = self.client.request(
                "POST", f"{base}?{urlencode(query)}", headers=WX_HEADERS, body=b""
            )
            text = response.text()
            blocked = classify_blocked_page(text, response.url, response.status)
            if blocked:
                raise SourceError(blocked, f"Homepage blocked: {blocked}", status="blocked")
            page, has_more = parse_homepage_payload(
                _json_payload(text, "Homepage"), target["biz"]
            )
            pages += 1
            articles.extend(page)
            if not has_more:
                break
            if not page:
                raise SourceError("pagination_stalled", "Homepage has_more with empty page")
            begin += len(page)
        return articles, pages, 0

    def _url_with_environment_query(
        self, source: dict[str, Any]
    ) -> tuple[str, bool]:
        specs = source.get("query_from_env", {})
        if not specs:
            return source["url"], False
        parts = urlsplit(source["url"])
        pairs = list(parse_qsl(parts.query, keep_blank_values=True))
        existing_names = {name for name, _ in pairs}
        for name, env_name in specs.items():
            if name in existing_names:
                raise SourceError(
                    "unsafe_secret_query",
                    "environment query parameter duplicates a configured parameter",
                )
            value = os.environ.get(env_name)
            if not value:
                raise SourceError(
                    "missing_secret",
                    f"required environment variable {env_name} is not set",
                )
            if len(value) > 4096 or any(
                ord(character) < 32 or ord(character) > 126
                for character in value
            ):
                raise SourceError(
                    "invalid_secret_query",
                    "environment-backed query contains forbidden characters",
                )
            pairs.append((name, value))
        return (
            urlunsplit(
                (
                    parts.scheme,
                    parts.netloc,
                    parts.path,
                    urlencode(pairs),
                    "",
                )
            ),
            True,
        )

    def _rss(
        self, target: dict[str, Any], source: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], int, int]:
        request_url, sensitive_url = self._url_with_environment_query(source)
        response = self.client.request(
            "GET",
            request_url,
            headers={"Accept": "application/rss+xml, application/atom+xml, text/xml"},
            sensitive_url=sensitive_url,
        )
        text = response.text()
        blocked = classify_blocked_page(text, response.url, response.status)
        if blocked:
            raise SourceError(blocked, f"feed blocked: {blocked}", status="blocked")
        if source.get("entry_link_mode") == "wechat_original_from_description":
            articles, rejected = parse_public_account_feed(
                response.body,
                target_biz=str(target.get("biz") or ""),
                expected_author=str(source.get("expected_author") or ""),
                timestamp_shift_hours=int(source.get("timestamp_shift_hours", 0)),
            )
            for article in articles:
                article["allow_content_fetch"] = source.get("fetch_content", True)
            return articles, 1, rejected
        articles = parse_feed(response.body)
        shift = int(source.get("timestamp_shift_hours", 0))
        if shift:
            for article in articles:
                published = parse_datetime(article.get("published_at"))
                if published:
                    article["published_at"] = (
                        published + timedelta(hours=shift)
                    ).isoformat().replace("+00:00", "Z")
        for article in articles:
            article["allow_content_fetch"] = source.get("fetch_content", True)
        return articles, 1, 0

    def _sitemap(
        self, source: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], int, int]:
        response = self.client.request(
            "GET",
            source["url"],
            headers={"Accept": "application/xml,text/xml,application/gzip"},
        )
        articles = parse_sitemap(
            response.body,
            maximum_uncompressed_bytes=self.defaults[
                "max_sitemap_uncompressed_bytes"
            ],
            url_pattern=source["url_pattern"],
            nested_https_url=source.get("nested_https_url", False),
            published_at_from_url=source.get("published_at_from_url", ""),
            maximum_articles=source.get(
                "max_articles", self.defaults["max_articles_per_source"]
            ),
        )
        for article in articles:
            article["allow_content_fetch"] = source.get("fetch_content", False)
        return articles, 1, 0

    def _json_api(
        self, source: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], int, int]:
        headers = {"Accept": "application/json"}
        sensitive_headers: set[str] = set()
        for header, spec in source.get("headers_from_env", {}).items():
            value = os.environ.get(spec["env"])
            if not value:
                raise SourceError(
                    "missing_secret",
                    f"required environment variable {spec['env']} is not set",
                )
            header_value = str(spec.get("prefix", "")) + value
            if len(header_value) > 8192 or any(
                ord(character) < 32 or ord(character) > 126
                for character in header_value
            ):
                raise SourceError(
                    "invalid_secret_header",
                    "environment-backed header contains forbidden characters",
                )
            headers[header] = header_value
            sensitive_headers.add(header)
        body = None
        if source["method"] == "POST":
            body = json.dumps(source.get("body", {}), separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        response = self.client.request(
            source["method"],
            source["url"],
            headers=headers,
            body=body,
            sensitive_headers=sensitive_headers,
        )
        return (
            parse_json_api(
                _json_payload(response.text(), "JSON API"),
                str(source.get("list_path", "")),
                source["field_map"],
            ),
            1,
            0,
        )

    def fetch_content(
        self,
        article: dict[str, Any],
        *,
        target_biz: str | None,
        text_limit: int,
    ) -> dict[str, Any]:
        """Fetch one discovered URL and classify content without bypass attempts."""
        try:
            response = self.client.request(
                "GET",
                article["url"],
                headers={"Accept": "text/html,application/xhtml+xml"},
            )
            text = response.text()
            blocked = classify_blocked_page(text, response.url, response.status)
            if blocked:
                return {"status": "blocked", "reason": blocked}
            content_type = response.headers.get("content-type", "")
            if content_type and "html" not in content_type.lower():
                return {"status": "unavailable", "reason": "non_html_response"}
            parsed = parse_article_html(text, response.url)
            if parsed.get("content_status") == "blocked":
                return {
                    "status": "blocked",
                    "reason": parsed.get("blocked_reason") or "blocked",
                }
            host = (urlsplit(response.url).hostname or "").lower()
            if host == "mp.weixin.qq.com" and not (
                re_search_js_content(text)
            ):
                return {"status": "unavailable", "reason": "missing_wechat_content"}
            parsed_biz = parsed.get("biz")
            if target_biz and parsed_biz and parsed_biz != target_biz:
                return {"status": "unavailable", "reason": "content_biz_mismatch"}
            content = clean_text(parsed.get("content_text"), text_limit)
            if len(content) < 120:
                return {"status": "unavailable", "reason": "content_too_short"}
            return {
                "status": "valid",
                "reason": None,
                "title": clean_text(parsed.get("title"), 500),
                "text": content,
                "published_at": parsed.get("published_at"),
                "canonical_url": canonicalize_url(
                    str(parsed.get("canonical_url") or response.url)
                ),
            }
        except SourceError as exc:
            return {"status": exc.status, "reason": exc.code}
        except (ValueError, TypeError, KeyError) as exc:
            return {"status": "unavailable", "reason": f"parse_error:{type(exc).__name__}"}


def re_search_js_content(text: str) -> bool:
    """Avoid importing a full browser just to confirm WeChat's content container."""
    compact = text.lower().replace(" ", "")
    return 'id="js_content"' in compact or "id='js_content'" in compact
