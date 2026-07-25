"""模块: Pola 微信公众号公网阅读解析器
功能: 解析微信公开列表、RSS/Atom、JSON API 与文章 HTML，并识别阻断页
日期: 2026-07-24
作者: Codex
依赖: Python 3.10+ 标准库
"""

from __future__ import annotations

import email.utils
import gzip
import html as html_lib
import heapq
import io
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree


ARTICLE_FIELDS = ("title", "url", "published_at", "source_summary", "biz", "mid", "idx", "sn")

def _mapping(payload: Any) -> Mapping[str, Any]:
    if isinstance(payload, (str, bytes, bytearray)):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ValueError("payload is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a JSON object")
    return payload

def _records(value: Any, label: str) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ValueError(f"{label} is not valid JSON") from exc
    if isinstance(value, Mapping):
        if any(key in value for key in ("title", "url", "link")):
            value = [value]
        else:
            value = list(value.values())
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"{label} must be a list of objects")
    return list(value)

def _dig(value: Any, path: str | None) -> Any:
    if path in (None, "", "."):
        return value
    current = value
    for part in str(path).split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current

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
    if not raw:
        return {"biz": "", "mid": "", "idx": "", "sn": ""}
    parts = urlsplit(raw)
    if (parts.hostname or "").rstrip(".").lower() != "mp.weixin.qq.com":
        return {"biz": "", "mid": "", "idx": "", "sn": ""}
    query = parse_qs(parts.query, keep_blank_values=True)
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

def _article(title: Any, url: Any, published_at: Any = "", source_summary: Any = "",
             explicit: Mapping[str, Any] | None = None,
             target_biz: str = "") -> dict[str, str]:
    clean_url = html_lib.unescape(str(url or "").strip())
    url_ids = _url_ids(clean_url)
    explicit = explicit or {}
    biz = _same_biz(target_biz, explicit.get("biz"), url_ids["biz"])
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

def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}

def parse_album_payload(payload: Any, target_biz: str
                        ) -> tuple[list[dict[str, str]], bool, dict[str, str] | None]:
    """Parse a public WeChat Album response and produce its next-page cursor."""
    data = _mapping(payload)
    ret = _dig(data, "base_resp.ret")
    if ret is None:
        raise ValueError("WeChat Album response is missing base_resp.ret")
    if ret not in (0, "0"):
        raise ValueError(f"WeChat Album API error: ret={ret}")
    response = data.get("getalbum_resp")
    if response is None:
        raise ValueError("WeChat Album response is missing getalbum_resp")
    if not isinstance(response, Mapping):
        raise ValueError("getalbum_resp must be an object")
    if "article_list" not in response:
        raise ValueError("getalbum_resp is missing article_list")
    items = _records(response["article_list"], "article_list")
    articles = [_article(
        item.get("title"), item.get("url") or item.get("link"),
        item.get("create_time") or item.get("sendtime") or item.get("publish_time"),
        item.get("digest") or item.get("summary"),
        {"biz": item.get("__biz") or item.get("biz"),
         "mid": item.get("msgid") or item.get("mid"),
         "idx": item.get("itemidx") or item.get("idx"), "sn": item.get("sn")},
        target_biz,
    ) for item in items]
    has_more = _truthy(response.get(
        "continue_flag", response.get("has_more", response.get("can_msg_continue", False))
    ))
    cursor: dict[str, str] | None = None
    if has_more:
        last = items[-1] if items else {}
        msgid = response.get("next_begin_msgid") or last.get("msgid") or last.get("mid")
        itemidx = response.get("next_begin_itemidx") or last.get("itemidx") or last.get("idx")
        if msgid not in (None, ""):
            cursor = {"begin_msgid": str(msgid), "begin_itemidx": str(itemidx or "1")}
    return articles, has_more, cursor

def parse_homepage_payload(payload: Any, target_biz: str
                           ) -> tuple[list[dict[str, str]], bool]:
    """Parse a public WeChat Homepage appmsg_list response."""
    data = _mapping(payload)
    ret = _dig(data, "base_resp.ret")
    if ret is None:
        raise ValueError("WeChat Homepage response is missing base_resp.ret")
    if ret not in (0, "0"):
        raise ValueError(f"WeChat Homepage API error: ret={ret}")
    if "appmsg_list" in data:
        raw_items = data["appmsg_list"]
    elif isinstance(data.get("data"), Mapping) and "appmsg_list" in data["data"]:
        raw_items = data["data"]["appmsg_list"]
    else:
        raise ValueError("WeChat Homepage response is missing appmsg_list")
    items = _records(raw_items, "appmsg_list")
    articles = [_article(
        item.get("title"), item.get("link") or item.get("url"),
        item.get("sendtime") or item.get("create_time") or item.get("publish_time"),
        item.get("digest") or item.get("summary"),
        {"biz": item.get("__biz") or item.get("biz"),
         "mid": item.get("mid") or item.get("appmsgid"),
         "idx": item.get("idx") or item.get("itemidx"), "sn": item.get("sn")},
        target_biz,
    ) for item in items]
    has_more = _truthy(data.get(
        "has_more", data.get("continue_flag", data.get("can_msg_continue", False))
    ))
    return articles, has_more


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _element_text(element: ElementTree.Element | None) -> str:
    return "" if element is None else "".join(element.itertext()).strip()


def _shift_timestamp(value: str, hours: int) -> str:
    if not value or not hours:
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    shifted = parsed.timestamp() + hours * 3600
    return datetime.fromtimestamp(shifted, timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


class _EmbeddedWechatParser(HTMLParser):
    """Read account metadata from an authorized RSS description fragment."""

    _FIELDS = {"article-title", "article-author", "article-intro"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.depths = {name: 0 for name in self._FIELDS}
        self.text = {name: [] for name in self._FIELDS}

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        for name in self._FIELDS:
            if self.depths[name]:
                self.depths[name] += 1
        marker = attributes.get("data-wct-type", "")
        if marker in self._FIELDS and not self.depths[marker]:
            self.depths[marker] = 1
        href = html_lib.unescape(attributes.get("href", "")).strip()
        if href:
            self.links.append(href)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for name in self._FIELDS:
            if self.depths[name]:
                self.depths[name] -= 1

    def handle_data(self, data: str) -> None:
        for name in self._FIELDS:
            if self.depths[name]:
                self.text[name].append(data)


def parse_embedded_wechat(
    fragment: str,
    *,
    target_biz: str,
    expected_author: str = "",
) -> dict[str, str]:
    """Extract exactly one target WeChat URL plus short provenance fields."""
    parser = _EmbeddedWechatParser()
    parser.feed(str(fragment or ""))
    links: list[str] = []
    for raw in parser.links:
        candidate = html_lib.unescape(raw).strip()
        if candidate.startswith("http://mp.weixin.qq.com/"):
            candidate = "https://" + candidate[len("http://") :]
        parts = urlsplit(candidate)
        if parts.scheme == "https" and (parts.hostname or "").lower() == "mp.weixin.qq.com":
            links.append(candidate)
    unique_links = list(dict.fromkeys(links))
    if len(unique_links) != 1:
        raise ValueError("RSS item must contain exactly one WeChat article link")
    original_url = unique_links[0]
    ids = _url_ids(original_url)
    if not ids["biz"] or ids["biz"] != target_biz:
        raise ValueError("embedded WeChat article biz does not match target")

    clean_fragment = _text(fragment)
    title = _text(" ".join(parser.text["article-title"]))
    author = _text(" ".join(parser.text["article-author"]))
    intro = _text(" ".join(parser.text["article-intro"]))
    title = re.sub(r"^原文标题\s*[:：]\s*", "", title).strip()
    author = re.sub(r"^原文作者\s*[:：]\s*", "", author).strip()
    if not title:
        match = re.search(
            r"原文标题\s*[:：]\s*(.+?)(?:\s+原文作者\s*[:：]|$)",
            clean_fragment,
        )
        title = _text(match.group(1)) if match else ""
    if not author:
        match = re.search(
            r"原文作者\s*[:：]\s*(.+?)(?:\s+原文链接\s*[:：]|$)",
            clean_fragment,
        )
        author = _text(match.group(1)) if match else ""
    if expected_author and author != expected_author:
        raise ValueError("embedded article author does not match expected author")
    return {
        "url": original_url,
        "title": title,
        "author": author,
        "summary": intro,
        **ids,
    }


def _parse_feed_records(xml_text: str | bytes) -> list[dict[str, str]]:
    """Parse RSS 2.x/RSS 1.0/Atom while explicitly rejecting DTD/entities."""
    if isinstance(xml_text, bytes):
        declaration = re.search(
            br"encoding\s*=\s*['\"]([A-Za-z0-9._-]+)", xml_text[:256], re.IGNORECASE
        )
        encoding = declaration.group(1).decode("ascii") if declaration else ""
        if not encoding and xml_text.startswith((b"\xff\xfe", b"\xfe\xff")):
            encoding = "utf-16"
        try:
            document: str | bytes = xml_text.decode(encoding) if encoding else xml_text
        except (LookupError, UnicodeDecodeError) as exc:
            raise ValueError("feed declares an unsupported encoding") from exc
        probe = document if isinstance(document, str) else document.decode("utf-8", "replace")
    else:
        probe = str(xml_text)
        document = probe
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", probe, re.IGNORECASE):
        raise ValueError("XML DOCTYPE and ENTITY declarations are not allowed")
    try:
        root = ElementTree.fromstring(document)
    except (ElementTree.ParseError, LookupError) as exc:
        raise ValueError("feed is not valid XML") from exc
    root_name = _local_name(root.tag)
    if root_name not in {"feed", "rss", "rdf"}:
        raise ValueError("XML root is not Atom, RSS, or RDF")
    entry_name = "entry" if root_name == "feed" else "item"
    entries = [node for node in root.iter() if _local_name(node.tag) == entry_name]
    articles: list[dict[str, str]] = []
    for entry in entries:
        children: dict[str, list[ElementTree.Element]] = {}
        for child in list(entry):
            children.setdefault(_local_name(child.tag), []).append(child)
        title = _element_text((children.get("title") or [None])[0])
        url = ""
        if entry_name == "entry":
            links = children.get("link", [])
            chosen = next(
                (node for node in links if node.attrib.get("rel", "alternate") == "alternate"),
                links[0] if links else None,
            )
            url = chosen.attrib.get("href", "") if chosen is not None else ""
        else:
            url = _element_text((children.get("link") or [None])[0])
        if not url:
            candidate = _element_text((children.get("guid") or children.get("id") or [None])[0])
            url = candidate if candidate.startswith(("http://", "https://")) else ""
        published = next(
            (
                _element_text(children[name][0])
                for name in ("pubdate", "published", "updated", "date")
                if children.get(name)
            ),
            "",
        )
        summary_html = next(
            (
                _element_text(children[name][0])
                for name in ("description", "summary", "content", "encoded")
                if children.get(name)
            ),
            "",
        )
        article = _article(title, url, published, summary_html)
        article["_source_summary_html"] = summary_html
        articles.append(article)
    return articles


def parse_feed(xml_text: str | bytes) -> list[dict[str, str]]:
    """Parse a normal RSS/Atom feed without changing the entry link."""
    records = _parse_feed_records(xml_text)
    for record in records:
        record.pop("_source_summary_html", None)
    return records


def parse_public_account_feed(
    xml_text: str | bytes,
    *,
    target_biz: str,
    expected_author: str = "",
    timestamp_shift_hours: int = 0,
) -> tuple[list[dict[str, str]], int]:
    """Normalize an authorized RSS feed whose descriptions embed WeChat originals."""
    records = _parse_feed_records(xml_text)
    accepted: list[dict[str, str]] = []
    rejected = 0
    for record in records:
        source_url = record["url"]
        try:
            embedded = parse_embedded_wechat(
                record.pop("_source_summary_html", ""),
                target_biz=target_biz,
                expected_author=expected_author,
            )
        except ValueError:
            rejected += 1
            continue
        accepted.append(
            {
                **record,
                "title": embedded["title"] or record["title"],
                "url": embedded["url"],
                "source_url": source_url,
                "original_url": embedded["url"],
                "source_author": embedded["author"],
                "source_summary": embedded["summary"] or record["source_summary"],
                "biz": embedded["biz"],
                "mid": embedded["mid"],
                "idx": embedded["idx"],
                "sn": embedded["sn"],
                "published_at": _shift_timestamp(
                    record["published_at"], timestamp_shift_hours
                ),
            }
        )
    return accepted, rejected


def _bounded_gzip(payload: bytes, maximum: int) -> bytes:
    if not payload.startswith(b"\x1f\x8b"):
        if len(payload) > maximum:
            raise ValueError("sitemap exceeds the decompressed byte limit")
        return payload
    chunks: list[bytes] = []
    total = 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
            while True:
                chunk = stream.read(min(64 * 1024, maximum + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise ValueError("sitemap exceeds the decompressed byte limit")
                chunks.append(chunk)
    except (EOFError, OSError) as exc:
        raise ValueError("sitemap gzip payload is invalid") from exc
    return b"".join(chunks)


def parse_sitemap(
    payload: bytes,
    *,
    maximum_uncompressed_bytes: int,
    url_pattern: str,
    nested_https_url: bool = False,
    published_at_from_url: str = "",
    maximum_articles: int = 1000,
) -> list[dict[str, str]]:
    """Parse a bounded XML/gzip sitemap into newest-first article candidates."""
    if maximum_articles < 1:
        raise ValueError("sitemap maximum_articles must be positive")
    document = _bounded_gzip(payload, maximum_uncompressed_bytes)
    probe = document.decode("utf-8", "replace")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", probe, re.IGNORECASE):
        raise ValueError("sitemap DOCTYPE and ENTITY declarations are not allowed")
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        raise ValueError("sitemap is not valid XML") from exc
    if _local_name(root.tag) != "urlset":
        raise ValueError("sitemap root must be urlset")
    url_re = re.compile(url_pattern)
    date_re = re.compile(published_at_from_url) if published_at_from_url else None
    newest: list[tuple[str, str, int, dict[str, str]]] = []
    seen_urls: set[str] = set()
    sequence = 0
    for node in root.iter():
        if _local_name(node.tag) != "loc":
            continue
        raw = html_lib.unescape(_element_text(node)).strip()
        if nested_https_url:
            nested_at = raw.find("https://", len("https://"))
            if nested_at >= 0:
                raw = raw[nested_at:]
        parts = urlsplit(raw)
        if parts.scheme != "https" or not parts.hostname or not url_re.search(raw):
            continue
        if raw in seen_urls:
            continue
        published = ""
        if date_re:
            match = date_re.search(raw)
            if not match:
                continue
            published = f"{match.group(1)}T00:00:00Z"
        seen_urls.add(raw)
        article = _article(
            parts.path.rstrip("/").rsplit("/", 1)[-1] or raw,
            raw,
            published,
            "",
        )
        entry = (published, raw, sequence, article)
        sequence += 1
        if len(newest) < maximum_articles:
            heapq.heappush(newest, entry)
        elif (published, raw) > (newest[0][0], newest[0][1]):
            heapq.heapreplace(newest, entry)
    newest.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [entry[3] for entry in newest]


def parse_json_api(payload: Any, list_path: str,
                   field_map: Mapping[str, str]) -> list[dict[str, str]]:
    """Normalize a user-configured remote JSON API without evaluating expressions."""
    if isinstance(payload, (str, bytes, bytearray)):
        try:
            data = json.loads(payload)
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ValueError("payload is not valid JSON") from exc
    else:
        data = payload
    if not isinstance(data, (Mapping, list)):
        raise ValueError("JSON API payload must be an object or array")
    selected = _dig(data, list_path)
    if selected is None:
        raise ValueError(f"{list_path or 'payload'} is missing or null")
    items = _records(selected, list_path or "payload")
    if not isinstance(field_map, Mapping):
        raise ValueError("field_map must be an object")
    articles: list[dict[str, str]] = []
    for item in items:
        mapped = {name: _dig(item, path) for name, path in field_map.items()
                  if isinstance(path, str)}
        articles.append(_article(
            mapped.get("title"), mapped.get("url"), mapped.get("published_at"),
            mapped.get("source_summary", mapped.get("summary")),
            {"biz": mapped.get("biz"), "mid": mapped.get("mid"),
             "idx": mapped.get("idx"), "sn": mapped.get("sn")},
        ))
    return articles


# Keep the public parsing surface in one module while isolating HTML complexity.
from .article_parser import classify_blocked_page, parse_article_html  # noqa: E402
