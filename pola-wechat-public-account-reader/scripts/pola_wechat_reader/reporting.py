"""Report aggregation and atomic JSON/Markdown persistence.

Module: reporting
Purpose: expose updates and monitoring blind spots without overstating coverage
Created: 2026-07-24
Author: Codex
Dependencies: Python standard library
"""

from __future__ import annotations

import json
import os
import html
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from .models import Article, SourceObservation


HEALTHY = {"ok", "observed_zero"}


def _coverage(observations: list[SourceObservation]) -> str:
    healthy = [item for item in observations if item.status in HEALTHY]
    if not healthy:
        return "unknown"
    if any(item.coverage == "complete" for item in healthy):
        return "complete"
    groups: dict[str, set[str]] = {}
    for item in healthy:
        groups.setdefault(item.independence_group, set()).update(item.article_keys)
    if len(groups) >= 2:
        values = list(groups.values())
        if all(value == values[0] for value in values[1:]):
            return "high_confidence"
    return "partial"


def _target_status(
    observations: list[SourceObservation],
    candidates: list[Article],
    new_articles: list[Article],
) -> str:
    if new_articles:
        return "new_articles"
    healthy = [item for item in observations if item.status in HEALTHY]
    failed = [item for item in observations if item.status not in HEALTHY]
    if not healthy:
        return "source_unavailable"
    if failed:
        return "partial_observation"
    if not candidates:
        return "observed_zero"
    return "no_new_articles_observed"


def build_report(
    *,
    run_id: str,
    started_at: str,
    finished_at: str,
    config_path: str,
    warnings: list[str],
    targets: list[dict[str, Any]],
    observations: list[SourceObservation],
    articles: list[Article],
    new_articles: list[Article],
    dry_run: bool,
    selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate target states. Free partial sources never produce `no_update`."""
    observations_by_target: dict[str, list[SourceObservation]] = defaultdict(list)
    candidates_by_target: dict[str, list[Article]] = defaultdict(list)
    new_by_target: dict[str, list[Article]] = defaultdict(list)
    for item in observations:
        observations_by_target[item.target_id].append(item)
    for item in articles:
        candidates_by_target[item.target_id].append(item)
    for item in new_articles:
        new_by_target[item.target_id].append(item)

    target_results: list[dict[str, Any]] = []
    for target in targets:
        if not target.get("enabled", True):
            continue
        target_observations = observations_by_target[target["id"]]
        target_candidates = candidates_by_target[target["id"]]
        target_new = new_by_target[target["id"]]
        target_results.append(
            {
                "target_id": target["id"],
                "name": target["name"],
                "wxid": target.get("wxid"),
                "biz": target.get("biz"),
                "priority": target["priority"],
                "status": _target_status(
                    target_observations, target_candidates, target_new
                ),
                "coverage": _coverage(target_observations),
                "candidate_count": len(target_candidates),
                "new_count": len(target_new),
                "sources": [item.to_dict() for item in target_observations],
            }
        )

    coverages = [item["coverage"] for item in target_results]
    if not coverages or all(item == "unknown" for item in coverages):
        overall_coverage = "unknown"
    elif all(item in {"complete", "high_confidence"} for item in coverages):
        overall_coverage = "high_confidence"
    else:
        overall_coverage = "partial"

    failed_sources = [
        item
        for item in observations
        if item.status not in HEALTHY
    ]
    if overall_coverage == "unknown":
        run_status = "failed"
    elif failed_sources:
        run_status = "degraded"
    else:
        run_status = "success"

    valid_content = sum(item.content_status == "valid" for item in new_articles)
    blocked_content = sum(item.content_status == "blocked" for item in new_articles)
    summaries = sum(item.summary_status != "not_generated" for item in new_articles)
    targets_with_updates = sum(item["new_count"] > 0 for item in target_results)
    targets_with_failures = sum(
        any(source["status"] not in HEALTHY for source in item["sources"])
        for item in target_results
    )
    targets_unknown = sum(
        item["coverage"] == "unknown" for item in target_results
    )
    report = {
        "schema_version": "1.1",
        "run_id": run_id,
        "run_status": run_status,
        "result": (
            "new_articles"
            if new_articles
            else (
                "coverage_incomplete"
                if overall_coverage == "unknown" or failed_sources
                else "no_new_articles_observed"
            )
        ),
        "coverage": overall_coverage,
        "dry_run": dry_run,
        "started_at": started_at,
        "finished_at": finished_at,
        "config_path": config_path,
        "selection": selection
        or {
            "selected_target_ids": [item["target_id"] for item in target_results],
            "selected_target_count": len(target_results),
            "configured_target_count": len(targets),
            "filters": {
                "target_ids": [],
                "exclude_target_ids": [],
                "priorities": [],
            },
        },
        "warnings": warnings,
        "counts": {
            "targets": len(target_results),
            "targets_with_updates": targets_with_updates,
            "targets_with_failures": targets_with_failures,
            "targets_unknown": targets_unknown,
            "sources": len(observations),
            "source_failures": len(failed_sources),
            "candidates": len(articles),
            "new": len(new_articles),
            "content_valid": valid_content,
            "content_blocked": blocked_content,
            "summaries_available": summaries,
            "candidates_rejected": sum(
                item.rejected_count for item in observations
            ),
            "sources_truncated": sum(item.truncated for item in observations),
        },
        "targets": target_results,
        "new_articles": [item.to_dict() for item in new_articles],
        "errors": [
            {
                "target_id": item.target_id,
                "source_key": item.source_key,
                "status": item.status,
                "code": item.error_code,
                "http_status": item.http_status,
                "message": item.error_message,
            }
            for item in failed_sources
        ],
    }
    return report


def _markdown_inline(value: Any) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
    text = html.escape(text, quote=False).replace("\\", "\\\\")
    for character in ("`", "*", "_", "[", "]", "#", "|", ">"):
        text = text.replace(character, "\\" + character)
    return text


def _markdown_block(value: Any) -> str:
    lines = str(value or "").replace("\r", "\n").splitlines() or [""]
    escaped: list[str] = []
    for line in lines:
        clean = _markdown_inline(line)
        if re.match(r"^(?:[-+]|\d+\.)\s", clean):
            clean = "\\" + clean
        escaped.append(clean)
    return "\n".join(escaped)


def _markdown_url(value: Any) -> str:
    """Percent-encode untrusted path/query delimiters before an angle link."""
    parts = urlsplit(str(value or ""))
    netloc = quote(parts.netloc, safe="[]:.%-_~")
    path = quote(
        parts.path,
        safe="/:@!$&'()*+,;=%-._~",
    )
    query = quote(
        parts.query,
        safe="=&/:@!$'()*+,;?%-._~",
    )
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def _markdown_audit_entries(values: Any) -> str:
    entries = list(values or [])
    if not entries:
        return "无"
    return "、".join(f"`{_markdown_inline(value)}`" for value in entries)


def _markdown(report: dict[str, Any]) -> str:
    private_sources = report["selection"].get(
        "private_mode_enabled_sources",
        [],
    )
    private_content_sources = report["selection"].get(
        "private_mode_content_sources",
        [],
    )
    permission_overrides = report["selection"].get(
        "permission_overrides",
        [],
    )
    lines = [
        "# 微信公众号公网监控报告",
        "",
        f"- 运行：`{report['run_id']}`",
        f"- 时间：{report['started_at']} → {report['finished_at']}",
        f"- 状态：`{report['run_status']}`",
        f"- 覆盖：`{report['coverage']}`",
        f"- 新文章：{report['counts']['new']}",
        f"- 来源故障：{report['counts']['source_failures']}",
        f"- 选中目标：{report['selection']['selected_target_count']}"
        f" / {report['selection']['configured_target_count']}",
        "- 来源策略："
        f"`{report['selection'].get('source_permission_policy', 'enforced')}`",
        "- 私人模式启用来源："
        f"{len(private_sources)}（{_markdown_audit_entries(private_sources)}）",
        "- 私人模式正文来源："
        f"{len(private_content_sources)}"
        f"（{_markdown_audit_entries(private_content_sources)}）",
        "- Permission override："
        f"{len(permission_overrides)}"
        f"（{_markdown_audit_entries(permission_overrides)}）",
        f"- Dry run：{'是' if report['dry_run'] else '否'}",
        "",
    ]
    if report["coverage"] == "unknown":
        lines.extend(
            [
                "> 监控盲区：所有可用来源均失败或被阻断，本报告不能判断是否有更新。",
                "",
            ]
        )
    elif not report["new_articles"]:
        lines.extend(
            [
                "> 本轮未观察到新文章；免费来源覆盖不完整，这不等于全网确定无更新。",
                "",
            ]
        )

    lines.extend(["## 目标状态", ""])
    for target in report["targets"]:
        lines.append(
            f"- **{_markdown_inline(target['name'])}** (`{target['target_id']}`)："
            f"`{target['status']}` / `{target['coverage']}`，"
            f"候选 {target['candidate_count']}，新增 {target['new_count']}"
        )
        for source in target["sources"]:
            detail = f"；{source['error_code']}" if source.get("error_code") else ""
            lines.append(
                f"  - `{source['source_type']}` `{source['status']}`"
                f"，窗口内 {source['article_count']} 篇{detail}"
            )

    lines.extend(["", "## 新文章", ""])
    if not report["new_articles"]:
        lines.append("_本轮没有首次发现的文章。_")
    for article in report["new_articles"]:
        lines.extend(
            [
                f"### {_markdown_inline(article['title'] or '无标题')}",
                "",
                f"- 公众号：{_markdown_inline(article['target_name'])}"
                f" (`{article['target_id']}`)",
                f"- 发布时间：{_markdown_inline(article['published_at'] or '未知')}",
                f"- 身份：`{article['identity_status']}`",
                f"- 正文：`{article['content_status']}`"
                + (
                    f" / `{_markdown_inline(article['content_reason'])}`"
                    if article.get("content_reason")
                    else ""
                ),
                f"- 原文：[打开文章](<{_markdown_url(article['url'])}>)",
                "",
                f"**自动摘要（`{article['summary_basis']}`）：**",
                "",
                _markdown_block(article.get("summary"))
                or "_没有可验证正文或来源摘要。_",
                "",
                "<details><summary>供 Agent 深化的摘要输入</summary>",
                "",
                _markdown_block(article.get("summary_input")) or "_无_",
                "",
                "</details>",
                "",
            ]
        )

    if report["errors"]:
        lines.extend(["## 来源异常", ""])
        for error in report["errors"]:
            lines.append(
                f"- `{error['target_id']}/{error['source_key']}`："
                f"`{error['status']}` / `{error['code'] or 'unknown'}`"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(data, encoding="utf-8")
    os.replace(temporary, path)


def write_reports(
    report: dict[str, Any],
    output_dir: str | os.PathLike[str],
    *,
    retention_days: int,
) -> tuple[str, str, str]:
    """Write immutable and latest reports, then remove expired report files."""
    directory = Path(output_dir).expanduser().resolve()
    json_text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    markdown_text = _markdown(report)
    json_path = directory / f"report-{report['run_id']}.json"
    markdown_path = directory / f"report-{report['run_id']}.md"
    _atomic_write(json_path, json_text)
    _atomic_write(markdown_path, markdown_text)
    _atomic_write(directory / "latest.json", json_text)
    _atomic_write(directory / "latest.md", markdown_text)
    _prune(directory, retention_days)
    return str(json_path), str(markdown_path), json_text


def _prune(directory: Path, retention_days: int) -> None:
    threshold = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for path in directory.glob("report-*.*"):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if modified < threshold:
                path.unlink()
        except OSError:
            continue
