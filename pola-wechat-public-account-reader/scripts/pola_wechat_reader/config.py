"""Configuration loader and safety validation.

Module: configuration
Purpose: reject ambiguous or dangerous monitor settings before network access
Created: 2026-07-24
Author: Codex
Dependencies: Python standard library
"""

from __future__ import annotations

import json
import os
import re
import ipaddress
import hashlib
import stat
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .http_client import validate_public_https_url
from .identity import SAFE_ID_RE, extract_wechat_identity
from .models import ConfigError, SourceError


DEFAULTS: dict[str, Any] = {
    "lookback_hours": 72,
    "timeout_seconds": 15,
    "max_run_seconds": 600,
    "max_response_bytes": 5 * 1024 * 1024,
    "max_pages": 3,
    "max_articles_per_source": 100,
    "max_candidates_per_target": 1000,
    "max_total_candidates": 5000,
    "fetch_content": True,
    "max_content_fetches": 10,
    "max_content_fetches_per_target": 3,
    "article_text_limit": 200 * 1024,
    "max_sitemap_uncompressed_bytes": 10 * 1024 * 1024,
    "summary_input_chars": 1800,
    "retention_days": 30,
    "retry_count": 1,
    "baseline_mode": "from_now",
    "source_permission_policy": "enforced",
}
MAX_TARGETS = 100
MAX_SOURCES_PER_TARGET = 10
MAX_CONFIG_BYTES = 1024 * 1024
SOURCE_TYPES = {
    "wechat_album",
    "wechat_homepage",
    "rss",
    "sitemap",
    "article_url",
    "json_api",
}
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
QUERY_NAME_RE = re.compile(r"^[A-Za-z0-9._~-]{1,64}$")
SENSITIVE_QUERY_NAMES = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "client_secret",
    "clientsecret",
    "credential",
    "credentials",
    "jwt",
    "key",
    "pass",
    "passwd",
    "password",
    "pass_ticket",
    "private_key",
    "privatekey",
    "refresh_token",
    "refreshtoken",
    "secret",
    "session",
    "sessionid",
    "token",
}
SOURCE_PERMISSION_POLICIES = {"enforced", "private_opt_in"}


def _sensitive_query_name(name: str) -> bool:
    lowered = name.strip().lower()
    normalized = re.sub(r"[^a-z0-9]", "", lowered)
    return (
        lowered in SENSITIVE_QUERY_NAMES
        or normalized in SENSITIVE_QUERY_NAMES
        or normalized.endswith(("password", "passwd", "secret", "token"))
    )


def _require_https(url: str, label: str) -> None:
    if any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise ConfigError(f"{label} contains a forbidden control character")
    parts = urlsplit(str(url or ""))
    if parts.scheme != "https" or not parts.hostname:
        raise ConfigError(f"{label} must be an absolute HTTPS URL")
    if parts.username or parts.password:
        raise ConfigError(f"{label} must not contain URL credentials")
    host = parts.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ConfigError(f"{label} must reference a public host")
    for name, value in parse_qsl(parts.query, keep_blank_values=True):
        if _sensitive_query_name(name) and value:
            raise ConfigError(
                f"{label} must inject sensitive query parameter {name!r} "
                "from an environment variable"
            )
    try:
        validate_public_https_url(url)
    except SourceError as exc:
        raise ConfigError(f"{label} must reference a safe public host") from exc
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return
    if not address.is_global:
        raise ConfigError(f"{label} must not reference a private or local address")


def _bounded_int(defaults: dict[str, Any], name: str, minimum: int, maximum: int) -> None:
    value = defaults.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"defaults.{name} must be an integer")
    if value < minimum or value > maximum:
        raise ConfigError(f"defaults.{name} must be between {minimum} and {maximum}")


def _strict_bool(
    mapping: dict[str, Any],
    name: str,
    *,
    default: bool,
    label: str,
) -> bool:
    value = mapping.get(name, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{label}.{name} must be boolean")
    return value


def _safe_optional_text(value: Any, label: str, *, limit: int = 500) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise ConfigError(f"{label} must be a string no longer than {limit} characters")
    if any(ord(character) < 32 for character in value):
        raise ConfigError(f"{label} contains a forbidden control character")
    return value.strip()


def _optional_source_int(
    source: dict[str, Any],
    name: str,
    minimum: int,
    maximum: int,
    label: str,
) -> None:
    if name not in source:
        return
    value = source[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{label}.{name} must be an integer")
    if value < minimum or value > maximum:
        raise ConfigError(
            f"{label}.{name} must be between {minimum} and {maximum}"
        )


def _source_key(source: dict[str, Any], position: int) -> str:
    if source.get("id"):
        return str(source["id"])
    source_type = source["type"]
    if source.get("album_id"):
        identity = str(source["album_id"])
    elif source.get("hid"):
        identity = str(source["hid"])
        if source.get("cid") is not None:
            identity += f"-{source['cid']}"
    elif source.get("url"):
        identity = hashlib.sha256(str(source["url"]).encode("utf-8")).hexdigest()[:12]
    else:
        identity = str(position)
    return f"{source_type}-{identity}"


def _validate_source(
    source: dict[str, Any],
    target: dict[str, Any],
    position: int,
    *,
    source_permission_policy: str,
) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise ConfigError(f"target {target['id']} source #{position} must be an object")
    source = deepcopy(source)
    source_type = source.get("type")
    if source_type not in SOURCE_TYPES:
        raise ConfigError(f"target {target['id']} has unsupported source type {source_type!r}")
    source_label = f"target {target['id']} source #{position}"
    configured_enabled = _strict_bool(
        source,
        "enabled",
        default=True,
        label=source_label,
    )
    enable_in_private_mode = _strict_bool(
        source,
        "enable_in_private_mode",
        default=False,
        label=source_label,
    )
    source["configured_enabled"] = configured_enabled
    source["enable_in_private_mode"] = enable_in_private_mode
    source["enabled_by_private_mode"] = bool(
        source_permission_policy == "private_opt_in"
        and enable_in_private_mode
        and not configured_enabled
    )
    source["enabled"] = configured_enabled or source["enabled_by_private_mode"]
    source["key"] = _source_key(source, position)
    if len(source["key"]) > 200 or not SAFE_ID_RE.fullmatch(source["key"]):
        raise ConfigError(f"target {target['id']} has an unsafe source id")

    target_biz = target.get("biz")
    if source_type == "wechat_album":
        if not target_biz or not str(source.get("album_id") or "").isdigit():
            raise ConfigError(
                f"target {target['id']} Album requires biz and numeric album_id"
            )
        reverse = str(source.get("is_reverse", "0"))
        if reverse not in {"0", "1"}:
            raise ConfigError(f"target {target['id']} Album is_reverse must be 0 or 1")
        source["is_reverse"] = reverse
    elif source_type == "wechat_homepage":
        if not target_biz or not str(source.get("hid") or "").isdigit():
            raise ConfigError(
                f"target {target['id']} Homepage requires biz and numeric hid"
            )
        if source.get("cid") is not None and not str(source["cid"]).isdigit():
            raise ConfigError(f"target {target['id']} Homepage cid must be numeric")
    elif source_type in {"rss", "sitemap", "article_url", "json_api"}:
        _require_https(str(source.get("url") or ""), f"{target['id']} {source_type}.url")

    _optional_source_int(source, "max_pages", 1, 20, source_label)
    _optional_source_int(source, "max_articles", 1, 5000, source_label)
    _optional_source_int(source, "max_future_hours", 0, 24, source_label)
    source["max_future_hours"] = source.get("max_future_hours", 24)
    if "fetch_content" in source:
        source["fetch_content"] = _strict_bool(
            source,
            "fetch_content",
            default=True,
            label=source_label,
        )
    else:
        source["fetch_content"] = source_type != "sitemap"
    fetch_content_in_private_mode = _strict_bool(
        source,
        "fetch_content_in_private_mode",
        default=False,
        label=source_label,
    )
    source["fetch_content_in_private_mode"] = fetch_content_in_private_mode
    source["fetch_content_by_private_mode"] = bool(
        source_permission_policy == "private_opt_in"
        and source["enabled"]
        and fetch_content_in_private_mode
        and not source["fetch_content"]
    )
    if source["fetch_content_by_private_mode"]:
        source["fetch_content"] = True

    permission_required = _strict_bool(
        source,
        "permission_required",
        default=False,
        label=source_label,
    )
    source["permission_required"] = permission_required
    permission_reference = _safe_optional_text(
        source.get("permission_reference"),
        f"{source_label}.permission_reference",
        limit=300,
    )
    source["permission_reference"] = permission_reference
    source["permission_override_active"] = bool(
        source["enabled"]
        and permission_required
        and not permission_reference
        and source_permission_policy == "private_opt_in"
    )
    if (
        source["enabled"]
        and permission_required
        and not permission_reference
        and not source["permission_override_active"]
    ):
        raise ConfigError(
            f"{source_label} requires a non-sensitive permission_reference"
        )

    if source_type == "article_url" and target_biz:
        found = extract_wechat_identity(source["url"]).get("__biz")
        if found and found != target_biz:
            raise ConfigError(
                f"target {target['id']} article_url biz does not match target biz"
            )

    if source_type == "rss":
        link_mode = str(source.get("entry_link_mode", "feed"))
        if link_mode not in {"feed", "wechat_original_from_description"}:
            raise ConfigError(f"{source_label}.entry_link_mode is invalid")
        source["entry_link_mode"] = link_mode
        if link_mode == "wechat_original_from_description" and not target_biz:
            raise ConfigError(
                f"{source_label} requires target biz for embedded WeChat links"
            )
        source["expected_author"] = _safe_optional_text(
            source.get("expected_author"),
            f"{source_label}.expected_author",
            limit=200,
        )
        query_from_env = source.get("query_from_env", {})
        if not isinstance(query_from_env, dict):
            raise ConfigError(f"{source_label}.query_from_env must be an object")
        normalized_query: dict[str, str] = {}
        configured_query_names = {
            name for name, _ in parse_qsl(urlsplit(source["url"]).query)
        }
        for query_name, env_name in query_from_env.items():
            if (
                not isinstance(query_name, str)
                or not QUERY_NAME_RE.fullmatch(query_name)
            ):
                raise ConfigError(f"{source_label} has an unsafe query parameter")
            if not isinstance(env_name, str) or not ENV_RE.fullmatch(env_name):
                raise ConfigError(f"{source_label} has an unsafe environment name")
            if query_name in configured_query_names:
                raise ConfigError(
                    f"{source_label}.query_from_env duplicates {query_name!r}"
                )
            normalized_query[query_name] = env_name
        source["query_from_env"] = normalized_query
        shift = source.get("timestamp_shift_hours", 0)
        if isinstance(shift, bool) or not isinstance(shift, int) or not -24 <= shift <= 24:
            raise ConfigError(
                f"{source_label}.timestamp_shift_hours must be between -24 and 24"
            )
        source["timestamp_shift_hours"] = shift
    if source_type == "sitemap":
        pattern = _safe_optional_text(
            source.get("url_pattern"),
            f"{source_label}.url_pattern",
        )
        if not pattern:
            raise ConfigError(f"{source_label}.url_pattern is required")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"{source_label}.url_pattern is invalid") from exc
        source["url_pattern"] = pattern
        date_pattern = _safe_optional_text(
            source.get("published_at_from_url"),
            f"{source_label}.published_at_from_url",
        )
        if date_pattern:
            try:
                compiled_date = re.compile(date_pattern)
            except re.error as exc:
                raise ConfigError(
                    f"{source_label}.published_at_from_url is invalid"
                ) from exc
            if compiled_date.groups != 1:
                raise ConfigError(
                    f"{source_label}.published_at_from_url requires one capture group"
                )
        source["published_at_from_url"] = date_pattern
        source["nested_https_url"] = _strict_bool(
            source,
            "nested_https_url",
            default=False,
            label=source_label,
        )

    completeness = source.get("completeness", "partial")
    if completeness not in {"partial", "complete"}:
        raise ConfigError(f"target {target['id']} source completeness is invalid")
    if completeness == "complete":
        if source_type != "json_api" or source.get("complete_source_acknowledged") is not True:
            raise ConfigError(
                f"target {target['id']} may mark only an audited json_api complete"
            )
    source["completeness"] = completeness
    source["independence_group"] = str(
        source.get("independence_group")
        or ("wechat_public" if source_type.startswith("wechat_") else source_type)
    )
    if (
        len(source["independence_group"]) > 200
        or not SAFE_ID_RE.fullmatch(source["independence_group"])
    ):
        raise ConfigError(
            f"target {target['id']} has an unsafe independence_group"
        )

    if source_type == "json_api":
        method = str(source.get("method", "GET")).upper()
        if method not in {"GET", "POST"}:
            raise ConfigError(f"target {target['id']} json_api method must be GET or POST")
        source["method"] = method
        if not isinstance(source.get("field_map"), dict):
            raise ConfigError(f"target {target['id']} json_api requires field_map")
        for required in ("title", "url"):
            if not source["field_map"].get(required):
                raise ConfigError(
                    f"target {target['id']} json_api field_map requires {required}"
                )
        header_specs = source.get("headers_from_env", {})
        if not isinstance(header_specs, dict):
            raise ConfigError(f"target {target['id']} headers_from_env must be an object")
        for header, spec in header_specs.items():
            if not isinstance(header, str) or not isinstance(spec, dict):
                raise ConfigError(f"target {target['id']} has invalid environment header")
            if not HEADER_NAME_RE.fullmatch(header):
                raise ConfigError(f"target {target['id']} has invalid header name")
            if header.strip().lower() in {"cookie", "set-cookie"}:
                raise ConfigError(f"target {target['id']} must not configure Cookie headers")
            env_name = spec.get("env")
            if not isinstance(env_name, str) or not ENV_RE.fullmatch(env_name):
                raise ConfigError(f"target {target['id']} has unsafe environment name")
            prefix = spec.get("prefix", "")
            if not isinstance(prefix, str) or len(prefix) > 32:
                raise ConfigError(f"target {target['id']} has invalid header prefix")
            if any(ord(character) < 32 or ord(character) > 126 for character in prefix):
                raise ConfigError(f"target {target['id']} has invalid header prefix")
    return source


def validate_config(
    data: dict[str, Any],
    *,
    private_use_override: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Return a normalized configuration and non-blocking warnings."""
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ConfigError("config.version must equal 1")
    defaults = {**DEFAULTS, **(data.get("defaults") or {})}
    source_permission_policy = defaults.get("source_permission_policy")
    if source_permission_policy not in SOURCE_PERMISSION_POLICIES:
        raise ConfigError(
            "defaults.source_permission_policy must be enforced or private_opt_in"
        )
    if private_use_override:
        source_permission_policy = "private_opt_in"
        defaults["source_permission_policy"] = source_permission_policy
    for name, low, high in (
        ("lookback_hours", 1, 24 * 365),
        ("timeout_seconds", 1, 120),
        ("max_run_seconds", 30, 3600),
        ("max_response_bytes", 1024, 20 * 1024 * 1024),
        ("max_pages", 1, 20),
        ("max_articles_per_source", 1, 1000),
        ("max_candidates_per_target", 1, 20000),
        ("max_total_candidates", 1, 20000),
        ("max_content_fetches", 0, 100),
        ("max_content_fetches_per_target", 0, 100),
        ("article_text_limit", 1024, 1024 * 1024),
        ("max_sitemap_uncompressed_bytes", 1024, 20 * 1024 * 1024),
        ("summary_input_chars", 100, 10000),
        ("retention_days", 1, 365),
        ("retry_count", 0, 3),
    ):
        _bounded_int(defaults, name, low, high)
    if not isinstance(defaults.get("fetch_content"), bool):
        raise ConfigError("defaults.fetch_content must be boolean")
    if defaults.get("baseline_mode") not in {"from_now", "backfill_window"}:
        raise ConfigError("defaults.baseline_mode must be from_now or backfill_window")

    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ConfigError("config.targets must be a non-empty list")
    if len(targets) > MAX_TARGETS:
        raise ConfigError(f"config.targets may contain at most {MAX_TARGETS} targets")

    normalized_targets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_biz: set[str] = set()
    warnings: list[str] = []
    if source_permission_policy == "private_opt_in":
        warnings.append(
            "private_opt_in source permission policy is active; "
            "technical safety and content-quality gates remain enforced"
        )
    for position, raw_target in enumerate(targets, start=1):
        if not isinstance(raw_target, dict):
            raise ConfigError(f"target #{position} must be an object")
        target = deepcopy(raw_target)
        target_id = str(target.get("id") or "")
        if not SAFE_ID_RE.fullmatch(target_id):
            raise ConfigError(f"target #{position} has invalid id")
        if target_id in seen_ids:
            raise ConfigError(f"duplicate target id: {target_id}")
        seen_ids.add(target_id)
        target["enabled"] = _strict_bool(
            target,
            "enabled",
            default=True,
            label=f"target {target_id}",
        )
        target["name"] = (
            _safe_optional_text(
                target.get("name"),
                f"target {target_id}.name",
                limit=200,
            )
            or target_id
        )
        target["wxid"] = (
            _safe_optional_text(
                target.get("wxid"),
                f"target {target_id}.wxid",
                limit=200,
            )
            or None
        )
        target["priority"] = str(target.get("priority", "normal"))
        if target["priority"] not in {"critical", "normal", "low"}:
            raise ConfigError(f"target {target_id} has invalid priority")
        biz = str(target.get("biz") or "").strip()
        target["biz"] = biz or None
        if target["enabled"] and biz:
            if biz in seen_biz:
                raise ConfigError(f"duplicate enabled target biz: {biz}")
            seen_biz.add(biz)

        raw_sources = target.get("sources", [])
        if not isinstance(raw_sources, list):
            raise ConfigError(f"target {target_id}.sources must be a list")
        if len(raw_sources) > MAX_SOURCES_PER_TARGET:
            raise ConfigError(
                f"target {target_id}.sources may contain at most "
                f"{MAX_SOURCES_PER_TARGET} sources"
            )
        target["sources"] = [
            _validate_source(
                source,
                target,
                index,
                source_permission_policy=source_permission_policy,
            )
            for index, source in enumerate(raw_sources, start=1)
        ]
        source_keys = [source["key"] for source in target["sources"]]
        if len(source_keys) != len(set(source_keys)):
            raise ConfigError(f"target {target_id} has duplicate source ids")
        enabled_sources = [source for source in target["sources"] if source["enabled"]]
        if target["enabled"] and not enabled_sources:
            raise ConfigError(f"enabled target {target_id} requires an enabled source")
        if target["enabled"] and len(enabled_sources) == 1:
            warnings.append(f"target {target_id} has only one source; coverage is partial")
        if target["enabled"]:
            for source in target["sources"]:
                if source["enabled_by_private_mode"]:
                    warnings.append(
                        "private mode enabled source "
                        f"{target_id}/{source['key']}"
                    )
                if source["fetch_content_by_private_mode"]:
                    warnings.append(
                        "private mode enabled content fetch for "
                        f"{target_id}/{source['key']}"
                    )
                if source["permission_override_active"]:
                    warnings.append(
                        "private permission override active for "
                        f"{target_id}/{source['key']}"
                    )
        if not target["enabled"]:
            warnings.append(f"target {target_id} is disabled")
        normalized_targets.append(target)

    if not any(target["enabled"] for target in normalized_targets):
        raise ConfigError("config requires at least one enabled target")

    return {"version": 1, "defaults": defaults, "targets": normalized_targets}, warnings


def load_config(
    path: str | os.PathLike[str],
    *,
    private_use_override: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    config_path = Path(path).expanduser().resolve()
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(config_path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigError("config path must be a regular file")
        if metadata.st_size > MAX_CONFIG_BYTES:
            raise ConfigError(f"config file exceeds {MAX_CONFIG_BYTES} bytes")
        chunks: list[bytes] = []
        remaining = MAX_CONFIG_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_CONFIG_BYTES:
            raise ConfigError(f"config file exceeds {MAX_CONFIG_BYTES} bytes")
        data = json.loads(payload.decode("utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {config_path}") from exc
    except ConfigError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read config: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    config, warnings = validate_config(
        data,
        private_use_override=private_use_override,
    )
    config["_path"] = str(config_path)
    return config, warnings
