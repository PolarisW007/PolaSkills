"""模块: Pola 微信文章正文解析器
功能: 提取公开文章 HTML 元数据与正文，并拦截验证码、登录和风控页面
日期: 2026-07-24
作者: Codex
依赖: Python 3.10+ 标准库
"""

from __future__ import annotations

import ast
import email.utils
import html as html_lib
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
              "link", "meta", "param", "source", "track", "wbr"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    raw = str(value)
    raw = re.sub(r"(?is)<(?:script|style)\b.*?</(?:script|style)>", " ", raw)
    raw = re.sub(r"(?s)<[^>]*>", " ", raw)
    return re.sub(r"\s+", " ", html_lib.unescape(raw)).strip()


def _timestamp(value: Any) -> str:
    if value in (None, "") or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        number = float(value)
        if number > 100_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, timezone.utc).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return str(value).strip()
    raw = str(value).strip()
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
        if parsed:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo:
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        pass
    return raw


def _url_ids(url: Any) -> dict[str, str]:
    raw = html_lib.unescape(str(url or "").strip())
    query = parse_qs(urlsplit(raw).query, keep_blank_values=True) if raw else {}
    return {
        "biz": (query.get("__biz") or query.get("biz") or [""])[0],
        "mid": (query.get("mid") or [""])[0],
        "idx": (query.get("idx") or [""])[0],
        "sn": (query.get("sn") or [""])[0],
    }


def _same_biz(*values: Any) -> str:
    identities = [str(value).strip() for value in values if value not in (None, "")]
    if len(set(identities)) > 1:
        raise ValueError(f"WeChat biz mismatch: {identities!r}")
    return identities[0] if identities else ""


def _article(title: Any, url: Any, published_at: Any, source_summary: Any,
             explicit: Mapping[str, Any]) -> dict[str, str]:
    clean_url = html_lib.unescape(str(url or "").strip())
    url_ids = _url_ids(clean_url)
    biz = _same_biz(explicit.get("biz"), url_ids["biz"])
    return {
        "title": _text(title),
        "url": clean_url,
        "published_at": _timestamp(published_at),
        "source_summary": _text(source_summary),
        "biz": biz,
        "mid": str(url_ids["mid"] or explicit.get("mid") or "").strip(),
        "idx": str(url_ids["idx"] or explicit.get("idx") or "").strip(),
        "sn": str(url_ids["sn"] or explicit.get("sn") or "").strip(),
    }


class _ArticleHTMLParser(HTMLParser):
    """Collect metadata and prioritized text containers without script execution."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self.title: list[str] = []
        self.buckets = {"wechat": [], "article": [], "main": [], "body": []}
        self.depths = {name: 0 for name in self.buckets}
        self.title_depth = 0
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        if self.skip_depth:
            self.skip_depth += 1
            return
        if tag in {"script", "style", "noscript", "template"}:
            self.skip_depth = 1
            return
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            if key and attributes.get("content"):
                self.meta.setdefault(key, attributes["content"])
        if tag == "link" and "canonical" in attributes.get("rel", "").lower():
            self.canonical = attributes.get("href", "")
        if tag == "title":
            self.title_depth += 1
        starts = {
            "wechat": attributes.get("id") == "js_content",
            "article": tag == "article",
            "main": tag == "main",
            "body": tag == "body",
        }
        for name in self.buckets:
            if self.depths[name] and tag not in _VOID_TAGS:
                self.depths[name] += 1
            elif starts[name]:
                self.depths[name] = 1
            if self.depths[name] and tag in {"br", "p", "div", "li", "h1", "h2", "h3"}:
                self.buckets[name].append("\n")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1
            return
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        if tag == "title" and self.title_depth:
            self.title_depth -= 1
        for name in self.buckets:
            if self.depths[name]:
                if tag in {"p", "div", "li", "h1", "h2", "h3"}:
                    self.buckets[name].append("\n")
                self.depths[name] -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.title_depth:
            self.title.append(data)
        for name in self.buckets:
            if self.depths[name]:
                self.buckets[name].append(data)


def _js_value(source: str, name: str) -> str:
    pattern = rf"(?:var\s+)?(?:window\.)?{re.escape(name)}\s*=\s*(['\"])((?:\\.|(?!\1).)*)\1"
    match = re.search(pattern, source, re.DOTALL)
    if not match:
        return ""
    literal = match.group(1) + match.group(2) + match.group(1)
    try:
        return html_lib.unescape(str(ast.literal_eval(literal))).strip()
    except (SyntaxError, ValueError):
        return html_lib.unescape(match.group(2).replace(r"\/", "/")).strip()


def classify_blocked_page(text: Any, final_url: str,
                          status: int | None) -> str | None:
    """Return a stable reason code for known access-control responses."""
    body = html_lib.unescape(str(text or ""))
    folded = body.casefold()
    url = str(final_url or "").casefold()
    if any(token in url for token in ("wappoc_appmsgcaptcha", "/mp/verifycode", "captcha")):
        return "captcha"
    if "环境异常" in body and ("完成验证" in body or "当前环境异常" in body):
        return "environment_abnormal"
    captcha_phrases = (
        "this page maybe require captcha", "complete the security check",
        "请输入验证码", "请进行安全验证", "完成验证后即可继续访问",
        "page maybe requiring captcha", "mmbizwap:secitptpage/verify.html",
        'id="js_verify"', "id='js_verify'", "tcaptcha.js",
    )
    if any(token in folded for token in captcha_phrases):
        return "captcha"
    if any(token in body for token in ("访问过于频繁", "操作过于频繁", "请求过于频繁")):
        return "rate_limited"
    if any(token in body for token in ("请在微信客户端打开链接", "请用微信客户端打开")):
        return "wechat_client_required"
    if (
        "机器之心·数据服务" in body
        and "爬数据" in body
        and "前往了解" in body
    ):
        return "publisher_data_service_gate"
    if (
        "文章库 | 机器之心" in body
        and "PRO会员通讯" in body
        and "AI Shortlist" in body
    ):
        return "publisher_data_service_gate"
    if any(token in body for token in ("登录后可查看", "请先登录", "需要登录后")):
        return "login_required"
    try:
        code = int(status) if status is not None else None
    except (TypeError, ValueError):
        code = None
    return {429: "rate_limited", 401: "login_required",
            403: "access_forbidden"}.get(code)


def parse_article_html(html: str | bytes, final_url: str) -> dict[str, Any]:
    """Extract metadata and never return blocked-page text as article content."""
    source = html.decode("utf-8", "replace") if isinstance(html, bytes) else str(html)
    blocked_reason = classify_blocked_page(source, final_url, 200)
    parser = _ArticleHTMLParser()
    parser.feed(source)
    canonical_url = html_lib.unescape(
        parser.canonical or parser.meta.get("og:url", "") or final_url
    ).strip()
    js = {key: _js_value(source, key) for key in (
        "biz", "mid", "idx", "sn", "ct", "publish_time", "msg_title", "msg_desc"
    )}
    canonical_ids, final_ids = _url_ids(canonical_url), _url_ids(final_url)
    biz = _same_biz(js["biz"], canonical_ids["biz"], final_ids["biz"])
    title = parser.meta.get("og:title") or js["msg_title"] or " ".join(parser.title)
    published = (
        parser.meta.get("article:published_time") or js["publish_time"] or js["ct"]
    )
    summary = (
        parser.meta.get("og:description") or parser.meta.get("description") or js["msg_desc"]
    )
    contents = [_text(" ".join(parser.buckets[name]))
                for name in ("wechat", "article", "main", "body")]
    content = next((candidate for candidate in contents if candidate), "")
    result: dict[str, Any] = _article(
        title, canonical_url, published, summary,
        {"biz": biz, "mid": js["mid"] or final_ids["mid"],
         "idx": js["idx"] or final_ids["idx"], "sn": js["sn"] or final_ids["sn"]},
    )
    result.update({
        "canonical_url": canonical_url,
        "content_text": "" if blocked_reason else content,
        "content_status": (
            "blocked" if blocked_reason else ("valid" if content else "unavailable")
        ),
        "blocked_reason": blocked_reason,
    })
    return result
