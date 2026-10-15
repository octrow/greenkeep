"""Finding history across runs: attempts, parking, the daily budget."""

from __future__ import annotations

import sqlite3
from pathlib import Path

MAX_ATTEMPTS = 3
COLUMNS = frozenset({"status", "attempts", "issue", "pr", "sha"})  # what set() may write
DEFAULT_DB = Path.home() / ".local/share/greenkeep/state.db"
SCHEMA = """CREATE TABLE IF NOT EXISTS finding (
    repo TEXT, fp TEXT, status TEXT DEFAULT 'open', attempts INT DEFAULT 0,
    issue INT, pr INT, updated TEXT DEFAULT CURRENT_TIMESTAMP, sha TEXT,
    PRIMARY KEY (repo, fp))"""
# columns added after the first release, for databases created before them
MIGRATIONS = {"sha": "ALTER TABLE finding ADD COLUMN sha TEXT"}


class State:
    def __init__(self, path: Path = DEFAULT_DB) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(SCHEMA)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(finding)")}
        for column, ddl in MIGRATIONS.items():
            if column not in columns:
                self.db.execute(ddl)

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
        if unknown := set(fields) - COLUMNS:  # column names are spliced into the SQL
            raise ValueError(f"unknown finding columns: {sorted(unknown)}")
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE finding SET {cols}, updated=CURRENT_TIMESTAMP "
                        "WHERE repo=? AND fp=?", (*fields.values(), repo, fp))
        self.db.commit()

    def attempts(self, repo: str, fp: str) -> int:
        row = self._row(repo, fp)
        return row[1] if row else 0

    def add_attempt(self, repo: str, fp: str) -> None:
        """Count one attempt; the last allowed one parks the finding."""
        n = self.attempts(repo, fp) + 1
        self.set(repo, fp, attempts=n, **({"status": "parked"} if n >= MAX_ATTEMPTS else {}))

    def merges(self, repo: str) -> list[tuple[str, int | None, str | None]]:
        """(fingerprint, PR, merge commit) of every live greenkeep merge in the repo."""
        return self.db.execute("SELECT fp, pr, sha FROM finding WHERE repo=? AND "
                               "status='merged'", (repo,)).fetchall()

    def last_merge(self, repo: str) -> str:
        """When greenkeep last merged in this repo ("" if never): oldest goes first."""
        row = self.db.execute("SELECT max(updated) FROM finding WHERE repo=? AND "
                              "status='merged'", (repo,)).fetchone()
        return row[0] or ""

    def merged_today(self) -> int:
        # `updated` is stored in UTC; the budget is per local day
        return self.db.execute("SELECT count(*) FROM finding WHERE status='merged' AND "
                               "date(updated, 'localtime') = date('now', 'localtime')"
                               ).fetchone()[0]
