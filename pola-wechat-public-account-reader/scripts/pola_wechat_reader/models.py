"""Shared models for public-account monitoring.

Module: domain models
Purpose: keep source observations and normalized articles explicit
Created: 2026-07-24
Author: Codex
Dependencies: Python standard library
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ConfigError(ValueError):
    """Raised before network access when a target configuration is unsafe."""


class RunLocked(RuntimeError):
    """Raised when another process already owns the monitor lock."""


class SourceError(RuntimeError):
    """A classified upstream failure that must not become a zero-update result."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: str = "source_unavailable",
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.http_status = http_status


@dataclass(slots=True)
class Article:
    """Normalized candidate from any public discovery source."""

    target_id: str
    target_name: str
    source_type: str
    source_key: str
    independence_group: str
    title: str
    url: str
    source_url: str | None = None
    original_url: str | None = None
    source_author: str | None = None
    published_at: str | None = None
    source_summary: str = ""
    biz: str | None = None
    mid: str | None = None
    idx: str | None = None
    sn: str | None = None
    article_key: str = ""
    identity_status: str = "unverified"
    discovered_at: str = ""
    content_status: str = "not_fetched"
    content_reason: str | None = None
    content_source_url: str | None = None
    content_title: str | None = None
    content_hash: str | None = None
    summary_status: str = "not_generated"
    summary_basis: str = "none"
    summary: str = ""
    summary_input: str = ""
    seen_before: bool = False
    allow_content_fetch: bool = True
    discovered_by: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe record without storing full article bodies."""
        return asdict(self)


@dataclass(slots=True)
class SourceObservation:
    """One target/source observation with honest result semantics."""

    target_id: str
    source_key: str
    source_type: str
    independence_group: str
    status: str
    coverage: str = "partial"
    observed_at: str = ""
    article_count: int = 0
    article_keys: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    http_status: int | None = None
    pages_fetched: int = 0
    rejected_count: int = 0
    truncated: bool = False
    stop_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def unavailable_observation(
    target: dict[str, Any],
    source: dict[str, Any],
    observed_at: str,
    *,
    code: str,
    message: str,
) -> SourceObservation:
    """Build a truthful observation for work skipped by a safety boundary."""
    return SourceObservation(
        target_id=target["id"],
        source_key=source["key"],
        source_type=source["type"],
        independence_group=source["independence_group"],
        status="source_unavailable",
        coverage="partial",
        observed_at=observed_at,
        error_code=code,
        error_message=message,
    )
