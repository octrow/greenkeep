"""Finding history across runs: attempts, parking, the daily budget."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

MAX_ATTEMPTS = 3
COLUMNS = frozenset({"status", "attempts", "issue", "pr", "sha", "title", "url"})  # what set() may write
DEFAULT_DB = Path.home() / ".local/share/greenkeep/state.db"
SCHEMA = """CREATE TABLE IF NOT EXISTS finding (
    repo TEXT, fp TEXT, status TEXT DEFAULT 'open', attempts INT DEFAULT 0,
    issue INT, pr INT, updated TEXT DEFAULT CURRENT_TIMESTAMP, sha TEXT, title TEXT, url TEXT,
    merged_at TEXT, PRIMARY KEY (repo, fp))"""
# per-repo and per-run marks: a repo's cool-down end, the day the last run finished
MARKS = "CREATE TABLE IF NOT EXISTS mark (key TEXT PRIMARY KEY, value TEXT)"
COOL_DOWN = timedelta(days=7)
BUDGET_FREE = "vuln:"  # security fingerprints: they neither need nor spend the budget
# columns added after the first release, for databases created before them
MIGRATIONS = {column: f"ALTER TABLE finding ADD COLUMN {column} TEXT"
              for column in ("sha", "title", "url", "merged_at")}
# before merged_at, `updated` was the merge time, and any later write moved it
BACKFILL_MERGED_AT = "UPDATE finding SET merged_at=updated WHERE status='merged'"


@dataclass(frozen=True)
class CoolDown:
    """Until when a repo is left alone, and what it and greenkeep looked like then: a change
    to either ends it early."""
    until: date
    repo_head: str
    tool_head: str


class State:
    def __init__(self, path: Path = DEFAULT_DB) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(SCHEMA)
        self.db.execute(MARKS)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(finding)")}
        for column, ddl in MIGRATIONS.items():
            if column not in columns:
                self.db.execute(ddl)
                if column == "merged_at":
                    self.db.execute(BACKFILL_MERGED_AT)
        self.db.commit()

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
        # merged_at, not `updated`, dates a merge: later writes (a backfilled sha) move `updated`
        merged = ", merged_at=CURRENT_TIMESTAMP" if fields.get("status") == "merged" else ""
        self.db.execute(f"UPDATE finding SET {cols}, updated=CURRENT_TIMESTAMP{merged} "
                        "WHERE repo=? AND fp=?", (*fields.values(), repo, fp))
        self.db.commit()

    def attempts(self, repo: str, fp: str) -> int:
        row = self._row(repo, fp)
        return row[1] if row else 0

    def add_attempt(self, repo: str, fp: str, *, last: bool = False) -> None:
        """Count one attempt; the last allowed one (or one marked `last`) parks the finding."""
        n = self.attempts(repo, fp) + 1
        park = last or n >= MAX_ATTEMPTS
        self.set(repo, fp, attempts=n, **({"status": "parked"} if park else {}))

    def open_issues(self, repo: str) -> list[tuple[str, int]]:
        """(fingerprint, issue) of every finding still waiting with an issue open for it."""
        return self.db.execute("SELECT fp, issue FROM finding WHERE repo=? AND issue IS NOT "
                               "NULL AND status IN ('open', 'parked')", (repo,)).fetchall()

    def merges(self, repo: str) -> list[tuple[str, int | None, str | None]]:
        """(fingerprint, PR, merge commit) of every live greenkeep merge in the repo."""
        return self.db.execute("SELECT fp, pr, sha FROM finding WHERE repo=? AND "
                               "status='merged'", (repo,)).fetchall()

    def rows(self) -> list[dict]:
        """Every finding greenkeep has touched, newest first (the dashboard's data)."""
        cur = self.db.execute("SELECT repo, fp, status, attempts, issue, pr, updated, title, "
                              "url FROM finding ORDER BY updated DESC")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]

    def last_merge(self, repo: str) -> str:
        """When greenkeep last merged in this repo ("" if never): oldest goes first."""
        row = self.db.execute("SELECT max(merged_at) FROM finding WHERE repo=? AND "
                              "status='merged'", (repo,)).fetchone()
        return row[0] or ""

    def merged_today(self) -> int:
        # merged_at is stored in UTC; the budget is per local day
        return self.db.execute("SELECT count(*) FROM finding WHERE status='merged' AND "
                               "date(merged_at, 'localtime') = date('now', 'localtime') "
                               "AND fp NOT LIKE ?", (BUDGET_FREE + "%",)).fetchone()[0]

    def mark(self, key: str) -> str:
        row = self.db.execute("SELECT value FROM mark WHERE key=?", (key,)).fetchone()
        return row[0] if row else ""

    def set_mark(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO mark (key, value) VALUES (?, ?)", (key, value))
        self.db.commit()

    def cool_down(self, repo: str, repo_head: str, tool_head: str) -> date:
        """Leave the repo alone for a week; returns the day it is worked again."""
        until = date.today() + COOL_DOWN
        self.set_mark(f"cool-down:{repo}", f"{until.isoformat()} {repo_head} {tool_head}")
        return until

    def cool_down_of(self, repo: str) -> CoolDown | None:
        until, _, heads = self.mark(f"cool-down:{repo}").partition(" ")
        if not until:
            return None
        repo_head, _, tool_head = heads.partition(" ")
        return CoolDown(date.fromisoformat(until), repo_head, tool_head)

    def lift_cool_down(self, repo: str) -> None:
        self.db.execute("DELETE FROM mark WHERE key=?", (f"cool-down:{repo}",))
        self.db.commit()

    def record_run(self, day: date | None = None) -> None:
        """The scheduled run that began on `day` (today) is done: the digest went out."""
        self.set_mark("run", (day or date.today()).isoformat())

    def ran_today(self) -> bool:
        return self.mark("run") == date.today().isoformat()
