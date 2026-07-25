"""SQLite state and cross-process run lock.

Module: persistence
Purpose: deduplicate articles and preserve source/run evidence transactionally
Created: 2026-07-24
Author: Codex
Dependencies: Python standard library (fcntl on Unix)
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Iterable

from .models import Article, RunLocked, SourceObservation


class RunLock:
    """Non-blocking file lock covering the whole monitor run."""

    def __init__(self, state_path: str | os.PathLike[str]) -> None:
        self.path = Path(str(state_path) + ".lock").expanduser().resolve()
        self.handle = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            self.handle.close()
            self.handle = None
            raise RunLocked(f"monitor already running for {self.path}") from exc
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()))
        self.handle.flush()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.handle is not None:
            try:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()


class StateStore:
    """Explicit transaction boundary: callers commit only after report persistence."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS runs (
              run_id TEXT PRIMARY KEY,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              status TEXT NOT NULL,
              coverage TEXT,
              report_json TEXT
            );
            CREATE TABLE IF NOT EXISTS articles (
              target_id TEXT NOT NULL,
              article_key TEXT NOT NULL,
              biz TEXT,
              mid TEXT,
              idx TEXT,
              sn TEXT,
              url TEXT NOT NULL,
              title TEXT NOT NULL,
              published_at TEXT,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              content_status TEXT NOT NULL,
              content_hash TEXT,
              summary_input TEXT,
              PRIMARY KEY(target_id, article_key)
            );
            CREATE TABLE IF NOT EXISTS source_observations (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL,
              target_id TEXT NOT NULL,
              source_key TEXT NOT NULL,
              source_type TEXT NOT NULL,
              status TEXT NOT NULL,
              coverage TEXT NOT NULL,
              article_count INTEGER NOT NULL,
              error_code TEXT,
              error_message TEXT,
              observed_at TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );
            CREATE TABLE IF NOT EXISTS source_checkpoints (
              target_id TEXT NOT NULL,
              source_key TEXT NOT NULL,
              last_run_id TEXT NOT NULL,
              last_status TEXT NOT NULL,
              last_observed_at TEXT NOT NULL,
              last_success_at TEXT,
              latest_published_at TEXT,
              consecutive_failures INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(target_id, source_key)
            );
            """
        )
        self._migrate_legacy_article_key()
        self.connection.commit()

    def _migrate_legacy_article_key(self) -> None:
        columns = self.connection.execute("PRAGMA table_info(articles)").fetchall()
        primary = [
            str(row["name"])
            for row in sorted(columns, key=lambda row: int(row["pk"]))
            if int(row["pk"])
        ]
        if primary != ["article_key"]:
            return
        self.connection.executescript(
            """
            ALTER TABLE articles RENAME TO articles_legacy_global_key;
            CREATE TABLE articles (
              target_id TEXT NOT NULL,
              article_key TEXT NOT NULL,
              biz TEXT,
              mid TEXT,
              idx TEXT,
              sn TEXT,
              url TEXT NOT NULL,
              title TEXT NOT NULL,
              published_at TEXT,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              content_status TEXT NOT NULL,
              content_hash TEXT,
              summary_input TEXT,
              PRIMARY KEY(target_id, article_key)
            );
            INSERT INTO articles(
              target_id,article_key,biz,mid,idx,sn,url,title,published_at,
              first_seen_at,last_seen_at,content_status,content_hash,summary_input
            )
            SELECT
              target_id,article_key,biz,mid,idx,sn,url,title,published_at,
              first_seen_at,last_seen_at,content_status,content_hash,summary_input
            FROM articles_legacy_global_key;
            DROP TABLE articles_legacy_global_key;
            """
        )

    def begin(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def existing_keys(
        self, keys: Iterable[tuple[str, str]]
    ) -> set[tuple[str, str]]:
        result: set[tuple[str, str]] = set()
        values = list(dict.fromkeys(keys))
        for offset in range(0, len(values), 400):
            chunk = values[offset : offset + 400]
            if not chunk:
                continue
            marks = ",".join("(?,?)" for _ in chunk)
            parameters = [value for pair in chunk for value in pair]
            rows = self.connection.execute(
                "SELECT target_id,article_key FROM articles "
                f"WHERE (target_id,article_key) IN ({marks})",
                parameters,
            )
            result.update(
                (str(row["target_id"]), str(row["article_key"])) for row in rows
            )
        return result

    def target_has_articles(self, target_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM articles WHERE target_id=? LIMIT 1", (target_id,)
        ).fetchone()
        return row is not None

    def record_run_start(self, run_id: str, started_at: str) -> None:
        self.connection.execute(
            "INSERT INTO runs(run_id, started_at, status) VALUES (?, ?, ?)",
            (run_id, started_at, "running"),
        )

    def record_observation(self, run_id: str, observation: SourceObservation) -> None:
        payload = observation.to_dict()
        self.connection.execute(
            """
            INSERT INTO source_observations(
              run_id,target_id,source_key,source_type,status,coverage,article_count,
              error_code,error_message,observed_at,payload_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                observation.target_id,
                observation.source_key,
                observation.source_type,
                observation.status,
                observation.coverage,
                observation.article_count,
                observation.error_code,
                observation.error_message,
                observation.observed_at,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )

    def upsert_article(self, article: Article) -> None:
        self.connection.execute(
            """
            INSERT INTO articles(
              article_key,target_id,biz,mid,idx,sn,url,title,published_at,
              first_seen_at,last_seen_at,content_status,content_hash,summary_input
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(target_id,article_key) DO UPDATE SET
              last_seen_at=excluded.last_seen_at,
              title=excluded.title,
              url=excluded.url
            """,
            (
                article.article_key,
                article.target_id,
                article.biz,
                article.mid,
                article.idx,
                article.sn,
                article.url,
                article.title,
                article.published_at,
                article.discovered_at,
                article.discovered_at,
                article.content_status,
                article.content_hash,
                article.summary_input,
            ),
        )

    def update_checkpoint(
        self,
        run_id: str,
        observation: SourceObservation,
        latest_published_at: str | None,
    ) -> None:
        success = observation.status in {"ok", "observed_zero"}
        self.connection.execute(
            """
            INSERT INTO source_checkpoints(
              target_id,source_key,last_run_id,last_status,last_observed_at,
              last_success_at,latest_published_at,consecutive_failures
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(target_id,source_key) DO UPDATE SET
              last_run_id=excluded.last_run_id,
              last_status=excluded.last_status,
              last_observed_at=excluded.last_observed_at,
              last_success_at=CASE
                WHEN excluded.last_success_at IS NOT NULL
                THEN excluded.last_success_at
                ELSE source_checkpoints.last_success_at
              END,
              latest_published_at=CASE
                WHEN excluded.latest_published_at IS NOT NULL
                THEN excluded.latest_published_at
                ELSE source_checkpoints.latest_published_at
              END,
              consecutive_failures=CASE
                WHEN excluded.last_success_at IS NOT NULL THEN 0
                ELSE source_checkpoints.consecutive_failures + 1
              END
            """,
            (
                observation.target_id,
                observation.source_key,
                run_id,
                observation.status,
                observation.observed_at,
                observation.observed_at if success else None,
                latest_published_at if success else None,
                0 if success else 1,
            ),
        )

    def finish_run(
        self,
        run_id: str,
        *,
        finished_at: str,
        status: str,
        coverage: str,
        report_json: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE runs
            SET finished_at=?, status=?, coverage=?, report_json=?
            WHERE run_id=?
            """,
            (finished_at, status, coverage, report_json, run_id),
        )

    def prune_history(self, before_iso: str) -> None:
        """Bound run evidence while retaining article keys and checkpoints."""
        self.connection.execute(
            """
            DELETE FROM source_observations
            WHERE run_id IN (SELECT run_id FROM runs WHERE started_at < ?)
            """,
            (before_iso,),
        )
        self.connection.execute(
            "DELETE FROM runs WHERE started_at < ?",
            (before_iso,),
        )
