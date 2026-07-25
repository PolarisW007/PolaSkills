"""Transactional persistence for a completed monitor run.

Module: run persistence
Purpose: atomically align reports, observations, articles and checkpoints
Created: 2026-07-24
Author: Codex
Dependencies: Python standard library and local storage/reporting modules
"""

from __future__ import annotations

from typing import Any

from .models import Article, SourceObservation
from .reporting import write_reports
from .storage import StateStore


def persist_run(
    store: StateStore,
    *,
    run_id: str,
    started_at: str,
    finished_at: str,
    report: dict[str, Any],
    observations: list[SourceObservation],
    articles: list[Article],
    output_dir: str,
    retention_days: int,
    prune_before: str,
) -> tuple[str, str]:
    """Write the report first and commit monitor state only when it succeeds."""
    store.begin()
    try:
        store.record_run_start(run_id, started_at)
        for observation in observations:
            store.record_observation(run_id, observation)
        for article in articles:
            store.upsert_article(article)
        for observation in observations:
            published = [
                item.published_at
                for item in articles
                if item.target_id == observation.target_id
                and observation.source_key in item.discovered_by
                and item.published_at
            ]
            store.update_checkpoint(
                run_id,
                observation,
                max(published) if published else None,
            )
        json_path, markdown_path, json_text = write_reports(
            report,
            output_dir,
            retention_days=retention_days,
        )
        store.finish_run(
            run_id,
            finished_at=finished_at,
            status=report["run_status"],
            coverage=report["coverage"],
            report_json=json_text,
        )
        store.prune_history(prune_before)
        store.commit()
        return json_path, markdown_path
    except Exception:
        store.rollback()
        raise
