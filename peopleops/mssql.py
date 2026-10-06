"""Optional SQL Server import of an immutable PeopleOps evidence snapshot.

No pyodbc import or SQL Server connection is needed for the SQLite application.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any


class SQLServerPublishError(RuntimeError):
    """Safe error for callers; connection details and source payloads are omitted."""


def _integer(value: Any, name: str) -> int:
    # bool and float must not silently become financial amounts or counts.
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    if not -(2**63) <= value < 2**63:
        raise ValueError(f"{name} is outside SQL Server bigint range")
    return value


def _text(value: Any, name: str, limit: int, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value.encode("utf-16-le")) // 2 > limit:
        raise ValueError(f"{name} must be text within the documented SQL length")
    return value


def publish_run(run: dict[str, Any], connection_string: str | None = None) -> dict[str, Any]:
    """Import a service.get_run() snapshot and request gated monthly publication.

    Returns run_id, published, publication_status, published_run_id, reused.
    A blocked run is imported as evidence but cannot replace the active month.
    The connection string is read from PEOPLEOPS_MSSQL_CONNECTION_STRING when
    omitted. Use ODBC Driver 18, Encrypt=yes and TrustServerCertificate=no.
    All inserts and publication are one transaction, rolled back on any error.
    """
    if not isinstance(run, dict):
        raise ValueError("run must be a complete service.get_run() snapshot")
    metadata = run["run"]
    metrics = run["metrics"]
    records = run["records"]
    findings = run["findings"]
    sources = run["sources"]
    run_id = _text(metadata["id"], "run.id", 64)
    period = _text(metadata["period"], "run.period", 7)
    source_hash = _text(metadata["source_hash"], "run.source_hash", 64)
    rule_version = _text(run["rule_version"], "rule_version", 80)
    if not re.fullmatch(r"[12]\d{3}-(?:0[1-9]|1[0-2])", period or ""):
        raise ValueError("run.period must be YYYY-MM")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", source_hash or ""):
        raise ValueError("run.source_hash must be a SHA-256 hex digest")
    source_hash = source_hash.lower()
    started_at = datetime.fromisoformat(metadata["started_at"].replace("Z", "+00:00"))
    if started_at.tzinfo is None:
        raise ValueError("run.started_at must include a timezone")
    critical_count = _integer(metadata["critical_count"], "run.critical_count")
    run_values = (
        run_id, period, source_hash, rule_version,
        _text(metadata["scenario"], "run.scenario", 32),
        "blocked" if critical_count else "eligible", started_at.isoformat(),
        *(_integer(metadata[name], f"run.{name}") for name in (
            "duration_ms", "input_rows", "accepted_rows", "rejected_rows", "critical_count", "warning_count"
        )),
        *(_integer(metrics[name], f"metrics.{name}") for name in (
            "headcount", "gross_cents", "expected_cents", "variance_cents", "overtime_minutes"
        )),
    )
    fact_values = [(
        run_id, _text(item["employee_id"], "record.employee_id", 64),
        _text(item["employee_name"], "record.employee_name", 200),
        _text(item["department"], "record.department", 120),
        *(_integer(item[name], f"record.{name}") for name in (
            "expected_cents", "gross_cents", "variance_cents", "overtime_minutes"
        )),
        _text(item["status"], "record.status", 32),
    ) for item in records]
    finding_values = [(
        run_id, _text(item["id"], "finding.id", 128),
        _text(item.get("employee_id"), "finding.employee_id", 64, nullable=True),
        _text(item.get("employee_name"), "finding.employee_name", 200, nullable=True),
        _text(item.get("department"), "finding.department", 120, nullable=True),
        _text(item["rule"], "finding.rule", 80),
        _text(item["severity"], "finding.severity", 16),
        _text(item["message"], "finding.message", 2000),
        _text(item["owner"], "finding.owner", 120),
        _text(item["remediation"], "finding.remediation", 2000),
        _integer(item["impact_cents"], "finding.impact_cents"),
    ) for item in findings]
    source_values = [(
        run_id, _text(item["name"], "source.name", 120),
        _text(item["type"], "source.type", 80),
        _integer(item["rows"], "source.rows"),
        _text(item["status"], "source.status", 32),
    ) for item in sources]

    configured_connection = connection_string or os.environ.get("PEOPLEOPS_MSSQL_CONNECTION_STRING")
    if not configured_connection:
        raise SQLServerPublishError("Set PEOPLEOPS_MSSQL_CONNECTION_STRING to enable the optional integration.")
    try:
        import pyodbc  # type: ignore[import-not-found]
    except ImportError:
        raise SQLServerPublishError("Install the mssql extra and Microsoft ODBC Driver 18 first.") from None

    connection = None
    try:
        connection = pyodbc.connect(configured_connection, autocommit=False, timeout=10)
        connection.timeout = 30
        cursor = connection.cursor()
        cursor.execute("SET XACT_ABORT ON; IF @@TRANCOUNT = 0 BEGIN TRANSACTION;")
        # Serializes identical imports including two processes using different local IDs.
        lock_key = f"PeopleOps:import:{period}:{source_hash}:{rule_version}"
        cursor.execute(
            "DECLARE @result int; EXEC @result = sys.sp_getapplock "
            "@Resource=?, @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=10000; "
            "IF @result < 0 THROW 51002, 'Could not acquire import lock.', 1;",
            lock_key,
        )
        cursor.execute(
            "SELECT RunId FROM dbo.EtlRun WHERE Period=? AND SourceHash=? AND RuleVersion=?",
            period, source_hash, rule_version,
        )
        existing = cursor.fetchone()
        reused = existing is not None
        if existing:
            run_id = existing[0]
        else:
            cursor.execute(
                "INSERT dbo.EtlRun(RunId,Period,SourceHash,RuleVersion,Scenario,Outcome,StartedAt,"
                "DurationMs,InputRows,AcceptedRows,RejectedRows,CriticalCount,WarningCount,"
                "Headcount,GrossCents,ExpectedCents,VarianceCents,OvertimeMinutes) "
                "VALUES (?,?,?,?,?,?,CONVERT(datetimeoffset(3), ?),?,?,?,?,?,?,?,?,?,?,?)",
                *run_values,
            )
            if fact_values:
                cursor.executemany(
                    "INSERT dbo.PayrollFact(RunId,EmployeeId,EmployeeName,Department,ExpectedCents,"
                    "GrossCents,VarianceCents,OvertimeMinutes,ReconciliationStatus) VALUES (?,?,?,?,?,?,?,?,?)",
                    fact_values,
                )
            if finding_values:
                cursor.executemany(
                    "INSERT dbo.QualityFinding(RunId,FindingId,EmployeeId,EmployeeName,Department,Rule,"
                    "Severity,Message,Owner,Remediation,ImpactCents) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    finding_values,
                )
            if source_values:
                cursor.executemany(
                    "INSERT dbo.RunSource(RunId,SourceName,SourceType,RowCount,SourceStatus) VALUES (?,?,?,?,?)",
                    source_values,
                )
            cursor.execute("INSERT dbo.RunEvidenceSeal(RunId) VALUES (?)", run_id)
        cursor.execute("EXEC dbo.PublishReviewedRun @RunId=?", run_id)
        while cursor.description is None:
            if not cursor.nextset():
                raise SQLServerPublishError("Publication procedure returned no result.")
        row = cursor.fetchone()
        if row is None:
            raise SQLServerPublishError("Publication procedure returned no result.")
        result = {
            "run_id": row[0], "published": bool(row[1]),
            "publication_status": row[2], "published_run_id": row[3], "reused": reused,
        }
        connection.commit()
        return result
    except SQLServerPublishError:
        if connection is not None:
            connection.rollback()
        raise
    except Exception:
        if connection is not None:
            connection.rollback()
        # ODBC errors may contain hosts, usernames or configuration. Never expose them.
        raise SQLServerPublishError("SQL Server import failed; the transaction was rolled back. Check database setup and connectivity.") from None
    finally:
        if connection is not None:
            connection.close()
