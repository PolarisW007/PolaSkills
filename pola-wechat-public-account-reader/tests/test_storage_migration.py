"""SQLite compatibility tests for target-scoped article identity.

Module: state migration harness
Purpose: preserve existing state while upgrading the article primary key
Created: 2026-07-24
Author: Codex
Dependencies: Python standard library
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from pola_wechat_reader.storage import StateStore  # noqa: E402


class StorageMigrationTests(unittest.TestCase):
    def test_legacy_global_article_key_is_migrated_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE articles (
                  article_key TEXT PRIMARY KEY,
                  target_id TEXT NOT NULL,
                  biz TEXT, mid TEXT, idx TEXT, sn TEXT,
                  url TEXT NOT NULL, title TEXT NOT NULL, published_at TEXT,
                  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                  content_status TEXT NOT NULL, content_hash TEXT,
                  summary_input TEXT
                );
                INSERT INTO articles VALUES (
                  'url:one','legacy',NULL,NULL,NULL,NULL,
                  'https://news.example/one','One',NULL,
                  '2026-01-01T00:00:00Z','2026-01-01T00:00:00Z',
                  'not_fetched',NULL,''
                );
                """
            )
            connection.commit()
            connection.close()

            store = StateStore(path)
            try:
                primary = [
                    row["name"]
                    for row in store.connection.execute(
                        "PRAGMA table_info(articles)"
                    ).fetchall()
                    if row["pk"]
                ]
                count = store.connection.execute(
                    "SELECT COUNT(*) FROM articles WHERE target_id='legacy'"
                ).fetchone()[0]
                self.assertEqual(primary, ["target_id", "article_key"])
                self.assertEqual(count, 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
