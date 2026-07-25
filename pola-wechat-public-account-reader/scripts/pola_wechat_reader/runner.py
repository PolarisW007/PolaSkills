"""End-to-end monitor orchestration.

Module: runner
Purpose: fetch, verify, deduplicate, retrieve, report and checkpoint one run
Created: 2026-07-24
Author: Codex
Dependencies: Python standard library and local runtime modules
"""

from __future__ import annotations

import secrets
import re
import time
from copy import deepcopy
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

from .config import _sensitive_query_name, load_config
from .http_client import HttpClient, validate_public_https_url
from .identity import (
    article_key,
    canonicalize_url,
    clean_text,
    content_hash,
    extract_wechat_identity,
    iso_datetime,
)
from .models import (
    Article,
    ConfigError,
    SourceError,
    SourceObservation,
    unavailable_observation,
)
from .parsers import classify_blocked_page
from .reporting import build_report, write_reports
from .run_persistence import persist_run
from .sources import SourceFetcher
from .storage import RunLock, StateStore


Clock = Callable[[], datetime]
PRIORITY_ORDER = {"critical": 0, "normal": 1, "low": 2}


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_id(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


def select_targets(
    config: dict[str, Any],
    *,
    target_ids: list[str] | None = None,
    exclude_target_ids: list[str] | None = None,
    priorities: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a config narrowed to enabled targets without changing the input."""
    selected_ids = list(dict.fromkeys(target_ids or []))
    excluded_ids = list(dict.fromkeys(exclude_target_ids or []))
    selected_priorities = list(dict.fromkeys(priorities or []))
    by_id = {target["id"]: target for target in config["targets"]}
    unknown = sorted((set(selected_ids) | set(excluded_ids)) - set(by_id))
    if unknown:
        raise ConfigError(f"unknown target ids: {', '.join(unknown)}")
    disabled_requested = [
        target_id
        for target_id in selected_ids
        if not by_id[target_id].get("enabled", True)
    ]
    if disabled_requested:
        raise ConfigError(
            "explicitly selected targets are disabled: "
            + ", ".join(disabled_requested)
        )
    invalid_priorities = [
        value for value in selected_priorities if value not in PRIORITY_ORDER
    ]
    if invalid_priorities:
        raise ConfigError(
            "invalid priorities: " + ", ".join(sorted(invalid_priorities))
        )
    include = set(selected_ids)
    exclude = set(excluded_ids)
    priority_filter = set(selected_priorities)
    targets = [
        target
        for target in config["targets"]
        if target.get("enabled", True)
        and (not include or target["id"] in include)
        and target["id"] not in exclude
        and (not priority_filter or target["priority"] in priority_filter)
    ]
    if not targets:
        raise ConfigError("target filters selected no enabled targets")
    narrowed = deepcopy(config)
    narrowed["targets"] = deepcopy(targets)
    metadata = {
        "selected_target_ids": [target["id"] for target in targets],
        "selected_target_count": len(targets),
        "configured_target_count": len(config["targets"]),
        "source_permission_policy": narrowed.get("defaults", {}).get(
            "source_permission_policy", "enforced"
        ),
        "private_mode_enabled_sources": [
            f"{target['id']}/{source['key']}"
            for target in targets
            for source in target["sources"]
            if source.get("enabled_by_private_mode")
        ],
        "private_mode_content_sources": [
            f"{target['id']}/{source['key']}"
            for target in targets
            for source in target["sources"]
            if source.get("fetch_content_by_private_mode")
        ],
        "permission_overrides": [
            f"{target['id']}/{source['key']}"
            for target in targets
            for source in target["sources"]
            if source.get("permission_override_active")
        ],
        "filters": {
            "target_ids": selected_ids,
            "exclude_target_ids": excluded_ids,
            "priorities": selected_priorities,
        },
    }
    return narrowed, metadata


def _public_url(value: Any, label: str) -> str | None:
    if value in (None, ""):
        return None
    raw = str(value).strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise ValueError(f"{label} contains a forbidden control character")
    try:
        original_parts = urlsplit(raw)
    except ValueError as exc:
        raise ValueError(f"{label} is malformed") from exc
    if original_parts.username or original_parts.password:
        raise ValueError(f"{label} must not contain URL credentials")
    for name, value in parse_qsl(
        original_parts.query, keep_blank_values=True
    ):
        if _sensitive_query_name(name) and value:
            raise ValueError(
                f"{label} contains sensitive query parameter {name!r}"
            )
    url = canonicalize_url(raw)
    try:
        validate_public_https_url(url)
    except SourceError as exc:
        raise ValueError(f"{label} is not a public HTTPS URL") from exc
    return url


def _normalize_candidate(
    target: dict[str, Any],
    source: dict[str, Any],
    raw: dict[str, Any],
    observed_at: str,
) -> Article:
    title = clean_text(raw.get("title"), 500)
    url = _public_url(raw.get("url"), "candidate URL") or ""
    if not title or not url:
        raise ValueError("candidate requires non-empty title and URL")
    source_url = _public_url(raw.get("source_url"), "candidate source URL")
    original_url = _public_url(raw.get("original_url"), "candidate original URL")
    host = (urlsplit(url).hostname or "").lower()
    url_identity = extract_wechat_identity(url)
    target_biz = target.get("biz")
    raw_biz = str(raw.get("biz") or "").strip() or None
    url_biz = url_identity.get("__biz")

    if target_biz and raw_biz and raw_biz != target_biz:
        raise ValueError("candidate biz does not match target")
    if target_biz and url_biz and url_biz != target_biz:
        raise ValueError("candidate URL __biz does not match target")
    if source["type"] in {"wechat_album", "wechat_homepage"} and target_biz:
        if host != "mp.weixin.qq.com" or url_biz != target_biz:
            raise ValueError("WeChat list candidate lacks verified target __biz")

    if target_biz and url_biz == target_biz:
        identity_status = "verified"
    elif host != "mp.weixin.qq.com":
        identity_status = "source_bound"
    else:
        identity_status = "unverified"

    normalized = {
        **raw,
        "url": url,
        "biz": url_biz or raw_biz,
        "mid": url_identity.get("mid") or raw.get("mid"),
        "idx": url_identity.get("idx") or raw.get("idx"),
        "sn": url_identity.get("sn") or raw.get("sn"),
        "published_at": iso_datetime(raw.get("published_at")),
    }
    key = article_key(normalized, target_biz)
    source_summary = clean_text(raw.get("source_summary"), 3000)
    if classify_blocked_page(source_summary, "", None):
        source_summary = ""
    return Article(
        target_id=target["id"],
        target_name=target["name"],
        source_type=source["type"],
        source_key=source["key"],
        independence_group=source["independence_group"],
        title=title,
        url=url,
        source_url=source_url,
        original_url=original_url,
        source_author=clean_text(raw.get("source_author"), 200) or None,
        published_at=normalized["published_at"],
        source_summary=source_summary,
        biz=normalized["biz"],
        mid=str(normalized["mid"] or "") or None,
        idx=str(normalized["idx"] or "") or None,
        sn=str(normalized["sn"] or "") or None,
        article_key=key,
        identity_status=identity_status,
        discovered_at=observed_at,
        allow_content_fetch=bool(
            raw.get("allow_content_fetch", source.get("fetch_content", True))
        ),
        discovered_by=[source["key"]],
    )


def _merge_articles(articles: list[Article]) -> list[Article]:
    merged: dict[tuple[str, str], Article] = {}
    for article in articles:
        scoped_key = (article.target_id, article.article_key)
        current = merged.get(scoped_key)
        if current is None:
            merged[scoped_key] = article
            continue
        if article.source_key not in current.discovered_by:
            current.discovered_by.append(article.source_key)
        if len(article.source_summary) > len(current.source_summary):
            current.source_summary = article.source_summary
        if not current.published_at and article.published_at:
            current.published_at = article.published_at
        if current.identity_status != "verified" and article.identity_status == "verified":
            current.identity_status = "verified"
        if not current.source_url and article.source_url:
            current.source_url = article.source_url
        if not current.original_url and article.original_url:
            current.original_url = article.original_url
        if not current.source_author and article.source_author:
            current.source_author = article.source_author
        current.allow_content_fetch = (
            current.allow_content_fetch or article.allow_content_fetch
        )
    return list(merged.values())


def _populate_content(
    fetcher: SourceFetcher,
    article: Article,
    *,
    target_biz: str | None,
    defaults: dict[str, Any],
    fetch_allowed: bool,
) -> None:
    if (
        not defaults["fetch_content"]
        or not article.allow_content_fetch
        or not fetch_allowed
    ):
        article.content_status = "not_fetched"
        article.summary_input = article.source_summary[: defaults["summary_input_chars"]]
        _populate_fallback_summary(article)
        return
    article.content_source_url = article.url
    result = fetcher.fetch_content(
        article.to_dict(),
        target_biz=target_biz,
        text_limit=defaults["article_text_limit"],
    )
    article.content_status = str(result.get("status") or "unavailable")
    article.content_reason = result.get("reason")
    if article.content_status == "valid":
        text = clean_text(result.get("text"), defaults["article_text_limit"])
        article.content_title = clean_text(result.get("title"), 500) or None
        if (
            article.content_title
            and (
                article.title == "Known article URL"
                or article.source_type == "sitemap"
            )
        ):
            article.title = article.content_title
        article.content_hash = content_hash(text)
        article.summary_input = text[: defaults["summary_input_chars"]]
        article.summary = _extractive_summary(text)
        article.summary_status = "generated"
        article.summary_basis = "full_text"
        parsed_published_at = iso_datetime(result.get("published_at"))
        if parsed_published_at:
            article.published_at = parsed_published_at
        if result.get("canonical_url"):
            try:
                article.url = _public_url(
                    result["canonical_url"], "content canonical URL"
                ) or article.url
            except ValueError:
                pass
    else:
        article.summary_input = article.source_summary[: defaults["summary_input_chars"]]
        _populate_fallback_summary(article)


def _extractive_summary(text: str, limit: int = 420) -> str:
    """Provide a deterministic cron-safe fallback; the Agent may refine it later."""
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[。！？.!?])\s*", clean_text(text))
        if len(item.strip()) >= 20
    ]
    selected: list[str] = []
    total = 0
    for sentence in sentences[:8]:
        if selected and total + len(sentence) > limit:
            break
        selected.append(sentence)
        total += len(sentence)
        if len(selected) == 3:
            break
    return "".join(selected)[:limit] or clean_text(text, limit)


def _populate_fallback_summary(article: Article) -> None:
    blocked = classify_blocked_page(article.source_summary, "", None)
    if not article.source_summary or blocked:
        if blocked:
            article.source_summary = ""
        article.summary = ""
        article.summary_input = ""
        article.summary_status = "not_generated"
        article.summary_basis = "none"
        return
    article.summary = article.source_summary[:420]
    article.summary_status = "limited"
    article.summary_basis = "source_summary"


def _content_fetch_plan(
    articles: list[Article],
    targets: list[dict[str, Any]],
    defaults: dict[str, Any],
) -> list[Article]:
    """Allocate bounded content fetches fairly, with critical targets first."""
    global_limit = defaults["max_content_fetches"]
    per_target_limit = defaults["max_content_fetches_per_target"]
    if global_limit <= 0 or per_target_limit <= 0:
        return []

    queues: dict[str, deque[Article]] = defaultdict(deque)
    for article in articles:
        if defaults["fetch_content"] and article.allow_content_fetch:
            queues[article.target_id].append(article)

    selected: list[Article] = []
    for priority in sorted(PRIORITY_ORDER, key=PRIORITY_ORDER.get):
        target_ids = [
            target["id"]
            for target in targets
            if target["priority"] == priority and queues[target["id"]]
        ]
        fetched_by_target = {target_id: 0 for target_id in target_ids}
        active = target_ids
        while active and len(selected) < global_limit:
            next_round: list[str] = []
            for target_id in active:
                if len(selected) >= global_limit:
                    break
                queue = queues[target_id]
                if queue and fetched_by_target[target_id] < per_target_limit:
                    selected.append(queue.popleft())
                    fetched_by_target[target_id] += 1
                if queue and fetched_by_target[target_id] < per_target_limit:
                    next_round.append(target_id)
            active = next_round
    return selected


def run_monitor(
    *,
    config_path: str,
    state_path: str,
    output_dir: str,
    dry_run: bool = False,
    lookback_hours: int | None = None,
    backfill: bool = False,
    clock: Clock | None = None,
    client: HttpClient | None = None,
    monotonic: Callable[[], float] | None = None,
    loaded_config: tuple[dict[str, Any], list[str]] | None = None,
    target_ids: list[str] | None = None,
    exclude_target_ids: list[str] | None = None,
    priorities: list[str] | None = None,
    private_use: bool = False,
) -> dict[str, Any]:
    """Execute one idempotent run and return report paths plus exit code."""
    loaded, warnings = loaded_config or load_config(
        config_path,
        private_use_override=private_use,
    )
    config, selection = select_targets(
        loaded,
        target_ids=target_ids,
        exclude_target_ids=exclude_target_ids,
        priorities=priorities,
    )
    defaults = config["defaults"]
    if lookback_hours is not None:
        if lookback_hours < 1 or lookback_hours > 24 * 365:
            raise ValueError("lookback_hours override is out of bounds")
        defaults["lookback_hours"] = lookback_hours
    now_fn = clock or (lambda: datetime.now(timezone.utc))
    monotonic_fn = monotonic or time.monotonic
    deadline_at = monotonic_fn() + defaults["max_run_seconds"]
    started = now_fn().astimezone(timezone.utc)
    started_at = _iso(started)
    run_id = _run_id(started)
    transport = client or HttpClient(
        timeout_seconds=defaults["timeout_seconds"],
        max_response_bytes=defaults["max_response_bytes"],
        retry_count=defaults["retry_count"],
        run_deadline_seconds=defaults["max_run_seconds"],
        monotonic=monotonic_fn,
    )
    fetcher = SourceFetcher(transport, defaults)

    with RunLock(state_path):
        observations: list[SourceObservation] = []
        candidates: list[Article] = []
        candidates_by_target: dict[str, int] = defaultdict(int)
        exhausted_targets: set[str] = set()
        stop_code: str | None = None
        for target in config["targets"]:
            for source in target["sources"]:
                if not source["enabled"]:
                    continue
                if target["id"] in exhausted_targets:
                    observation = unavailable_observation(
                        target,
                        source,
                        started_at,
                        code="target_candidate_limit_reached",
                        message=(
                            "source skipped because the per-target candidate "
                            "limit was reached"
                        ),
                    )
                    observation.truncated = True
                    observation.stop_reason = "target_candidate_limit"
                    raw_articles = []
                elif stop_code or monotonic_fn() >= deadline_at:
                    stop_code = stop_code or "run_deadline_exceeded"
                    observation = unavailable_observation(
                        target,
                        source,
                        started_at,
                        code=stop_code,
                        message="source skipped after the run safety limit was reached",
                    )
                    observation.stop_reason = stop_code
                    raw_articles = []
                else:
                    observation, raw_articles = fetcher.fetch(
                        target,
                        source,
                        now=started,
                        observed_at=started_at,
                        lookback_hours=defaults["lookback_hours"],
                    )
                    if observation.error_code == "run_deadline_exceeded":
                        stop_code = "run_deadline_exceeded"
                normalized: list[Article] = []
                if observation.status in {"ok", "observed_zero"}:
                    last_error = ""
                    for raw in raw_articles:
                        try:
                            normalized.append(
                                _normalize_candidate(
                                    target, source, raw, started_at
                                )
                            )
                        except ValueError as exc:
                            observation.rejected_count += 1
                            last_error = str(exc)[:500]
                    if raw_articles and not normalized:
                        observation.status = "source_unavailable"
                        observation.error_code = "identity_or_schema_error"
                        observation.error_message = (
                            last_error
                            or "all source candidates failed identity validation"
                        )
                        observation.article_count = 0
                    elif observation.rejected_count:
                        observation.error_message = (
                            f"{observation.rejected_count} candidate(s) rejected "
                            "by identity or URL validation"
                        )

                target_remaining = (
                    defaults["max_candidates_per_target"]
                    - candidates_by_target[target["id"]]
                )
                if len(normalized) > max(0, target_remaining):
                    normalized = normalized[: max(0, target_remaining)]
                    observation.truncated = True
                    observation.stop_reason = "target_candidate_limit"
                    observation.coverage = "partial"
                    exhausted_targets.add(target["id"])
                if (
                    len(candidates) + len(normalized)
                    > defaults["max_total_candidates"]
                ):
                    stop_code = "candidate_limit_exceeded"
                    observation.status = "source_unavailable"
                    observation.error_code = stop_code
                    observation.error_message = (
                        "source skipped because the per-run candidate limit was reached"
                    )
                    observation.article_count = 0
                    observation.truncated = True
                    observation.stop_reason = "run_candidate_limit"
                    normalized = []
                observation.article_keys = [item.article_key for item in normalized]
                observation.article_count = len(normalized)
                if not normalized and observation.status == "ok":
                    observation.status = "observed_zero"
                observations.append(observation)
                candidates.extend(normalized)
                candidates_by_target[target["id"]] += len(normalized)
                if (
                    candidates_by_target[target["id"]]
                    >= defaults["max_candidates_per_target"]
                ):
                    exhausted_targets.add(target["id"])

        articles = _merge_articles(candidates)
        store = None if dry_run else StateStore(state_path)
        try:
            existing = set() if store is None else store.existing_keys(
                (item.target_id, item.article_key) for item in articles
            )
            target_has_history = {
                target["id"]: bool(store and store.target_has_articles(target["id"]))
                for target in config["targets"]
                if target["enabled"]
            }
            baseline_mode = defaults.get("baseline_mode", "from_now")
            new_articles: list[Article] = []
            for article in articles:
                article.seen_before = (
                    article.target_id,
                    article.article_key,
                ) in existing
                first_target_run = not target_has_history.get(article.target_id, False)
                if article.seen_before:
                    continue
                if first_target_run and baseline_mode == "from_now" and not backfill and not dry_run:
                    continue
                new_articles.append(article)

            target_biz = {
                target["id"]: target.get("biz")
                for target in config["targets"]
                if target["enabled"]
            }
            content_plan = _content_fetch_plan(
                new_articles, config["targets"], defaults
            )
            planned_ids = {id(article) for article in content_plan}
            for article in content_plan:
                fetch_allowed = monotonic_fn() < deadline_at
                _populate_content(
                    fetcher,
                    article,
                    target_biz=target_biz.get(article.target_id),
                    defaults=defaults,
                    fetch_allowed=fetch_allowed,
                )
                if not fetch_allowed and monotonic_fn() >= deadline_at:
                    article.content_status = "unavailable"
                    article.content_reason = "run_deadline_exceeded"
            for article in new_articles:
                if id(article) in planned_ids:
                    continue
                _populate_content(
                    fetcher,
                    article,
                    target_biz=target_biz.get(article.target_id),
                    defaults=defaults,
                    fetch_allowed=False,
                )

            finished_at = _iso(now_fn().astimezone(timezone.utc))
            report = build_report(
                run_id=run_id,
                started_at=started_at,
                finished_at=finished_at,
                config_path=str(Path(config_path).expanduser().resolve()),
                warnings=warnings,
                targets=config["targets"],
                observations=observations,
                articles=articles,
                new_articles=new_articles,
                dry_run=dry_run,
                selection=selection,
            )

            if store is not None:
                json_path, markdown_path = persist_run(
                    store,
                    run_id=run_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    report=report,
                    observations=observations,
                    articles=articles,
                    output_dir=output_dir,
                    retention_days=defaults["retention_days"],
                    prune_before=_iso(
                        started - timedelta(days=defaults["retention_days"])
                    ),
                )
            else:
                json_path, markdown_path, _ = write_reports(
                    report,
                    output_dir,
                    retention_days=defaults["retention_days"],
                )
        finally:
            if store is not None:
                store.close()

    return {
        "report": report,
        "json_path": json_path,
        "markdown_path": markdown_path,
        "exit_code": 1 if report["run_status"] == "failed" else 0,
    }
