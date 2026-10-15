"""Finding history across runs: attempts, parking, the daily budget."""

from __future__ import annotations

import sqlite3
from pathlib import Path

MAX_ATTEMPTS = 3
DEFAULT_DB = Path.home() / ".local/share/greenkeep/state.db"
SCHEMA = """CREATE TABLE IF NOT EXISTS finding (
    repo TEXT, fp TEXT, status TEXT DEFAULT 'open', attempts INT DEFAULT 0,
    issue INT, pr INT, updated TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (repo, fp))"""


class State:
    def __init__(self, path: Path = DEFAULT_DB) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(SCHEMA)

    def _row(self, repo: str, fp: str) -> tuple | None:
        return self.db.execute("SELECT status, attempts, issue FROM finding "
                               "WHERE repo=? AND fp=?", (repo, fp)).fetchone()

    def status(self, repo: str, fp: str) -> str | None:
        row = self._row(repo, fp)
        return row[0] if row else None

    def issue(self, repo: str, fp: str) -> int | None:
        row = self._row(repo, fp)
        return row[2] if row else None

    def is_workable(self, repo: str, fp: str) -> bool:
        return self.status(repo, fp) in (None, "open")

    def set(self, repo: str, fp: str, **fields: object) -> None:
        self.db.execute("INSERT OR IGNORE INTO finding (repo, fp) VALUES (?, ?)", (repo, fp))
        for k, v in fields.items():  # keys come from code, never from input
            self.db.execute(f"UPDATE finding SET {k}=?, updated=CURRENT_TIMESTAMP "
                            "WHERE repo=? AND fp=?", (v, repo, fp))
        self.db.commit()

    def add_attempt(self, repo: str, fp: str) -> int:
        """Count one attempt; the last allowed one parks the finding."""
        row = self._row(repo, fp)
        n = (row[1] if row else 0) + 1
        self.set(repo, fp, attempts=n, **({"status": "parked"} if n >= MAX_ATTEMPTS else {}))
        return n

    def merged_today(self) -> int:
        # `updated` is stored in UTC; the budget is per local day
        return self.db.execute("SELECT count(*) FROM finding WHERE status='merged' AND "
                               "date(updated, 'localtime') = date('now', 'localtime')"
                               ).fetchone()[0]
