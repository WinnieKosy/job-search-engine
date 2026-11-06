from __future__ import annotations

import datetime as dt
import sqlite3

from .config import DATA
from .models import Job


class Tracker:
    def __init__(self):
        self.db = sqlite3.connect(DATA / "tracker.db")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                url        TEXT PRIMARY KEY,
                title      TEXT,
                company    TEXT,
                location   TEXT,
                source     TEXT,
                tool       TEXT,
                status     TEXT,
                fit_score  INTEGER,
                fit_reason TEXT,
                folder     TEXT,
                updated_at TEXT
            )""")

    def status(self, url: str) -> str | None:
        row = self.db.execute("SELECT status FROM jobs WHERE url = ?", (url,)).fetchone()
        return row[0] if row else None

    def save(self, job: Job, status: str, tool: str = "", folder: str = "") -> None:
        self.db.execute(
            """
            INSERT INTO jobs (url, title, company, location, source, tool, status,
                              fit_score, fit_reason, folder, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (url) DO UPDATE SET
                title      = excluded.title,
                company    = excluded.company,
                location   = excluded.location,
                status     = excluded.status,
                fit_score  = excluded.fit_score,
                fit_reason = excluded.fit_reason,
                tool       = COALESCE(NULLIF(excluded.tool, ''), tool),
                folder     = COALESCE(NULLIF(excluded.folder, ''), folder),
                updated_at = excluded.updated_at
            """,
            (job.url, job.title, job.company, job.location, job.source, tool, status,
             job.fit_score, job.fit_reason, folder, dt.datetime.now().isoformat(timespec="seconds")))
        self.db.commit()

    def found(self, min_score: int) -> list[Job]:
        rows = self.db.execute(
            """
            SELECT url, title, company, location, source, fit_score, fit_reason
            FROM jobs
            WHERE status = 'found' AND fit_score >= ?
            ORDER BY fit_score DESC
            """, (min_score,))
        return [Job(url=r[0], title=r[1], company=r[2], location=r[3], source=r[4],
                    fit_score=r[5], fit_reason=r[6]) for r in rows]

    def rows(self):
        return self.db.execute(
            """
            SELECT status, fit_score, company, title, url, updated_at
            FROM jobs
            ORDER BY updated_at DESC
            """).fetchall()

    def applied(self, company: str | None = None) -> list[tuple[str, str, str, str, str]]:
        """Jobs you submitted: (company, title, url, applied_at, folder), grouped by company."""
        sql = """
            SELECT company, title, url, updated_at, folder
            FROM jobs
            WHERE status = 'submitted'
        """
        args: tuple = ()
        if company:
            sql += " AND company LIKE ?"
            args = (f"%{company}%",)
        sql += " ORDER BY company COLLATE NOCASE, updated_at DESC"
        return self.db.execute(sql, args).fetchall()
