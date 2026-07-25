"""Identity, URL and time normalization helpers.

Module: article identity
Purpose: verify WeChat biz ownership and build stable deduplication keys
Created: 2026-07-24
Author: Codex
Dependencies: Python standard library
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_KEYS = {
    "scene",
    "sessionid",
    "subscene",
    "clicktime",
    "enterid",
    "ascene",
    "devicetype",
    "version",
    "nettype",
    "lang",
    "fontscale",
    "exportkey",
    "pass_ticket",
    "wx_header",
}
WECHAT_ID_KEYS = ("__biz", "mid", "idx", "sn")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def clean_text(value: Any, limit: int | None = None) -> str:
    """Collapse HTML-ish whitespace into readable plain text."""
    text = html.unescape(str(value or ""))
    text = re.sub(r"<(?:br|/p|/div|/li)\b[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\u200b", "").replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = text.strip()
    return text[:limit] if limit is not None else text


def extract_wechat_identity(url: str) -> dict[str, str | None]:
    """Extract stable WeChat identifiers from a public article URL."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
        values = (
            dict(parse_qsl(parts.query, keep_blank_values=True))
            if host == "mp.weixin.qq.com"
            else {}
        )
    except ValueError:
        values = {}
    return {key: values.get(key) for key in WECHAT_ID_KEYS}


def canonicalize_url(url: str) -> str:
    """Remove fragments and known tracking parameters while preserving identity."""
    raw = str(url or "").strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    if raw.startswith("http://mp.weixin.qq.com"):
        raw = "https://" + raw[len("http://") :]
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").rstrip(".").lower()
    port = parts.port
    netloc = f"[{host}]" if ":" in host else host
    if port and not (scheme == "https" and port == 443):
        netloc = f"{netloc}:{port}"

    pairs: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        low = key.lower()
        if low.startswith("utm_") or low in TRACKING_KEYS:
            continue
        pairs.append((key, value))
    if host == "mp.weixin.qq.com":
        order = {name: pos for pos, name in enumerate(WECHAT_ID_KEYS)}
        pairs.sort(key=lambda item: (order.get(item[0], 99), item[0], item[1]))
        scheme = "https"
    else:
        pairs.sort()
    return urlunsplit((scheme, netloc, parts.path or "/", urlencode(pairs), ""))


def article_key(article: dict[str, Any], target_biz: str | None = None) -> str:
    """Build a stable key, preferring native WeChat identifiers."""
    biz = str(article.get("biz") or target_biz or "")
    mid = str(article.get("mid") or "")
    idx = str(article.get("idx") or "")
    sn = str(article.get("sn") or "")
    host = (urlsplit(str(article.get("url") or "")).hostname or "").rstrip(".").lower()
    if host == "mp.weixin.qq.com" and biz and mid and idx and sn:
        return f"wx:{biz}:{mid}:{idx}:{sn}"
    canonical = canonicalize_url(str(article.get("url") or ""))
    if canonical:
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"url:{digest}"
    raw = "\x1f".join(
        [biz, str(article.get("published_at") or ""), str(article.get("title") or "")]
    )
    return "fallback:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    return hashlib.sha256(clean_text(text).encode("utf-8")).hexdigest()


def parse_datetime(value: Any) -> datetime | None:
    """Parse Unix timestamps, RFC dates and ISO-8601 into aware UTC datetimes."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_datetime(value: Any) -> str | None:
    parsed = parse_datetime(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")
