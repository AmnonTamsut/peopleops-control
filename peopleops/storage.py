"""Local SQLite audit storage and cross-process single-writer protection."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
from time import monotonic, sleep
from typing import Any, Iterator


class AuditStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "peopleops.sqlite3"
        with self.writer(), self.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, period TEXT NOT NULL, source_hash TEXT NOT NULL,
                    rule_version TEXT NOT NULL, started_at TEXT NOT NULL, evidence TEXT NOT NULL,
                    UNIQUE(period, source_hash, rule_version)
                );
                CREATE INDEX IF NOT EXISTS ix_runs_period ON runs(period, started_at);
                CREATE TABLE IF NOT EXISTS publications (
                    period TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id)
                );
                CREATE TABLE IF NOT EXISTS reconciliation (
                    run_id TEXT NOT NULL REFERENCES runs(id), employee_id TEXT NOT NULL,
                    department TEXT NOT NULL, gross_cents INTEGER NOT NULL, expected_cents INTEGER NOT NULL,
                    variance_cents INTEGER NOT NULL, overtime_minutes INTEGER NOT NULL,
                    PRIMARY KEY(run_id, employee_id)
                );
                CREATE INDEX IF NOT EXISTS ix_reconciliation_department ON reconciliation(run_id, department);
                CREATE TABLE IF NOT EXISTS findings (
                    run_id TEXT NOT NULL REFERENCES runs(id), id TEXT NOT NULL,
                    employee_id TEXT NOT NULL, rule TEXT NOT NULL, severity TEXT NOT NULL,
                    evidence TEXT NOT NULL, PRIMARY KEY(run_id, id)
                );
                CREATE TRIGGER IF NOT EXISTS immutable_runs_update BEFORE UPDATE ON runs
                BEGIN SELECT RAISE(ABORT, 'Run audit is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_runs_delete BEFORE DELETE ON runs
                BEGIN SELECT RAISE(ABORT, 'Run audit is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_reconciliation_update BEFORE UPDATE ON reconciliation
                BEGIN SELECT RAISE(ABORT, 'Reconciliation evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_reconciliation_delete BEFORE DELETE ON reconciliation
                BEGIN SELECT RAISE(ABORT, 'Reconciliation evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_findings_update BEFORE UPDATE ON findings
                BEGIN SELECT RAISE(ABORT, 'Finding evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_findings_delete BEFORE DELETE ON findings
                BEGIN SELECT RAISE(ABORT, 'Finding evidence is immutable'); END;
            """)

    @contextmanager
    def writer(self) -> Iterator[None]:
        with (self.data_dir / ".pipeline.lock").open("a+b") as handle:
            if os.name == "nt":
                import msvcrt

                # Windows byte-range locks require an existing byte and a stable
                # file position. A bounded retry matches SQLite's timeout.
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                deadline = monotonic() + 30
                while True:
                    handle.seek(0)
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if monotonic() >= deadline:
                            raise TimeoutError("Another pipeline writer holds the data directory lock")
                        sleep(0.05)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def by_hash(self, period: str, source_hash: str, rule_version: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT evidence FROM runs WHERE period=? AND source_hash=? AND rule_version=?",
                               (period, source_hash, rule_version)).fetchone()
            return json.loads(row[0]) if row else None

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT evidence FROM runs WHERE id=?", (run_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def list_runs(self, period: str | None = None) -> list[dict[str, Any]]:
        with self.connection() as conn:
            query = "SELECT evidence FROM runs"
            args: tuple = ()
            if period:
                query += " WHERE period=?"
                args = (period,)
            rows = conn.execute(query + " ORDER BY rowid DESC", args).fetchall()
            return [json.loads(row[0])["run"] for row in rows]

    def published(self, period: str) -> str | None:
        with self.connection() as conn:
            row = conn.execute("SELECT run_id FROM publications WHERE period=?", (period,)).fetchone()
            return row[0] if row else None

    def save(self, evidence: dict[str, Any], rule_version: str) -> None:
        run = evidence["run"]
        with self.connection() as conn:
            conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)", (
                run["id"], run["period"], run["source_hash"], rule_version,
                run["started_at"], json.dumps(evidence, ensure_ascii=False, allow_nan=False)))
            conn.executemany("INSERT INTO reconciliation VALUES (?,?,?,?,?,?,?)", [
                (run["id"], r["employee_id"], r["department"], r["gross_cents"], r["expected_cents"],
                 r["variance_cents"], r["overtime_minutes"]) for r in evidence["records"]])
            conn.executemany("INSERT INTO findings VALUES (?,?,?,?,?,?)", [
                (run["id"], f["id"], f["employee_id"], f["rule"], f["severity"], json.dumps(f, ensure_ascii=False))
                for f in evidence["findings"]])
            if run["critical_count"] == 0:
                conn.execute("INSERT INTO publications VALUES (?,?) ON CONFLICT(period) DO UPDATE SET run_id=excluded.run_id",
                             (run["period"], run["id"]))
