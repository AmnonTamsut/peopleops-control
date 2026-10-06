"""HR/payroll reconciliation with immutable evidence and guarded publication."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from time import perf_counter
from typing import Any
import uuid

import pandas as pd

from .fixtures import source_fixture
from .rules import expected_gross, valid_employee
from .storage import AuditStore

RULE_VERSION = "simplified-gross-v1"
DEFAULT_PERIOD = "2026-09"


def _period(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("period must use YYYY-MM")
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError as exc:
        raise ValueError("period must be a valid YYYY-MM month") from exc
    return value


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class PeopleOpsService:
    def __init__(self, data_dir: Path):
        self.store = AuditStore(data_dir)

    def run(self, period: str, scenario: str = "review", employees: list[dict] | None = None) -> dict:
        period = _period(period)
        if employees is not None and (not isinstance(employees, list) or not all(isinstance(row, dict) for row in employees)):
            raise ValueError("employees must be a list of employee objects")
        with self.store.writer():
            clock = perf_counter()
            started_at = datetime.now(timezone.utc).isoformat()
            sources = source_fixture(period, scenario, employees)
            try:
                serialized = json.dumps({"rule_version": RULE_VERSION, "sources": sources}, sort_keys=True, separators=(",", ":"), allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("employees must contain finite JSON values") from exc
            source_hash = hashlib.sha256(serialized.encode()).hexdigest()
            existing = self.store.by_hash(period, source_hash, RULE_VERSION)
            if existing:
                self._verify_snapshots(existing["sources"])
                existing["run"]["reused"] = True
                existing["metrics"]["published_run_id"] = self.store.published(period)
                return existing
            sources, lineage = self._snapshot_sources(source_hash, sources, employees is not None)
            evidence = self._transform(period, sources)
            for source in evidence["sources"]:
                source.update(lineage[source["name"]])
            critical = sum(f["severity"] == "critical" for f in evidence["findings"])
            warning = sum(f["severity"] == "warning" for f in evidence["findings"])
            run_id = str(uuid.uuid4())
            evidence["run"] = {
                "id": run_id, "period": period, "scenario": scenario,
                "status": "blocked" if critical else "review" if warning else "published",
                "started_at": started_at, "duration_ms": max(1, int((perf_counter() - clock) * 1000)),
                "source_hash": source_hash, "input_rows": sum(len(rows) for rows in sources.values()),
                "accepted_rows": evidence.pop("accepted_rows"), "rejected_rows": evidence.pop("rejected_rows"),
                "critical_count": critical, "warning_count": warning, "reused": False,
            }
            evidence["metrics"]["published_run_id"] = self.store.published(period) if critical else run_id
            self.store.save(evidence, RULE_VERSION)
            return evidence

    def get_run(self, run_id: str) -> dict | None:
        return self.store.get(run_id)

    def _verify_snapshots(self, sources: list[dict]) -> None:
        """Detect changed evidence; never silently repair or rewrite a snapshot."""
        for source in sources:
            if "path" not in source or "sha256" not in source:
                continue  # Older local audits may predate source file lineage.
            path = self.store.data_dir / source["path"]
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
                raise ValueError(f"Source snapshot integrity check failed for {source['name']}")

    def _snapshot_sources(self, source_hash: str, sources: dict[str, list[dict]], supplied_hr: bool) -> tuple[dict[str, list[dict]], dict[str, dict]]:
        """Atomically retain real source files, then extract their saved contents."""
        snapshot_root = self.store.data_dir / "snapshots"
        snapshot_root.mkdir(exist_ok=True)
        destination = snapshot_root / source_hash
        content = {"employees.json": json.dumps(sources["employees"], sort_keys=True, ensure_ascii=False,
                                                allow_nan=False, indent=2).encode("utf-8")}
        for name in ("attendance", "payroll"):
            content[f"{name}.csv"] = pd.DataFrame(sources[name]).to_csv(index=False, lineterminator="\n").encode("utf-8")
        if not destination.exists():
            staging = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=snapshot_root))
            try:
                for filename, payload in content.items():
                    with (staging / filename).open("wb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                # The writer lock protects this rename across concurrent runs.
                staging.rename(destination)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        lineage = {}
        for filename, payload in content.items():
            path = destination / filename
            checksum = hashlib.sha256(payload).hexdigest()
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
                raise ValueError(f"Source snapshot integrity check failed for {filename}")
            name = path.stem
            lineage[name] = {"path": path.relative_to(self.store.data_dir).as_posix(), "sha256": checksum,
                             "type": ("HR API snapshot (JSON)" if supplied_hr else "Synthetic HR snapshot (JSON)")
                             if name == "employees" else "Synthetic CSV export"}
        extracted = {"employees": json.loads((destination / "employees.json").read_text(encoding="utf-8"))}
        # Explicit integer dtypes avoid float coercion in financial extraction.
        for name, amount_key in (("attendance", "overtime_minutes"), ("payroll", "gross_cents")):
            extracted[name] = pd.read_csv(destination / f"{name}.csv", keep_default_na=False,
                                          dtype={"employee_id": str, "period": str, amount_key: "int64"}).to_dict("records")
        return extracted, lineage

    def dashboard(self, period: str | None = None) -> dict:
        if period is not None:
            period = _period(period)
        runs = self.store.list_runs(period)
        if runs:
            evidence = self.store.get(runs[0]["id"])
            assert evidence is not None
            evidence["metrics"]["published_run_id"] = self.store.published(evidence["period"])
            evidence["runs"] = runs
            return evidence
        return {
            "period": period or DEFAULT_PERIOD, "run": None,
            "metrics": {"headcount": 0, "gross_cents": 0, "expected_cents": 0, "variance_cents": 0,
                        "overtime_minutes": 0, "quality_score": 100.0, "published_run_id": None},
            "departments": [], "findings": [], "records": [], "runs": [], "sources": [],
        }

    def _transform(self, period: str, sources: dict[str, list[dict]]) -> dict:
        findings: list[dict] = []
        rejected: set[tuple[str, int]] = set()
        invalid_keys: dict[str, set[str]] = {name: set() for name in sources}
        raw_roster = {row.get("employee_id"): row for row in sources["employees"]
                      if isinstance(row.get("employee_id"), str)}

        def finding(employee_id: str, rule: str, severity: str, message: str, owner: str,
                    remediation: str, impact: int = 0) -> None:
            employee = raw_roster.get(employee_id, {})
            findings.append({
                "id": f"F-{len(findings) + 1:03}", "employee_id": employee_id,
                "employee_name": str(employee.get("employee_name", "Unknown employee")),
                "department": str(employee.get("department", "Unassigned")), "rule": rule,
                "severity": severity, "message": message, "owner": owner,
                "remediation": remediation, "impact_cents": impact,
            })

        def quarantine(name: str, index: int, employee_id: str, rule: str, message: str) -> None:
            rejected.add((name, index))
            invalid_keys[name].add(employee_id)
            finding(employee_id, rule, "critical", message,
                    "HR Operations" if name == "employees" else "Time & Attendance" if name == "attendance" else "Payroll",
                    f"Correct the {name} source record and rerun this month; do not edit stored run evidence.")

        # Validate source contracts before DataFrame construction: invalid values must
        # never be silently coerced into floating point monetary calculations.
        for name, rows in sources.items():
            keys = [row.get("employee_id") for row in rows]
            counts = Counter(key for key in keys if isinstance(key, str))
            duplicates = {key for key, count in counts.items() if count > 1}
            for duplicate in sorted(duplicates):
                finding(duplicate, "duplicate_source_key", "critical", f"Duplicate employee key in {name} source.",
                        "Data Integration", f"Resolve duplicate {name} keys before rerunning.")
                invalid_keys[name].add(duplicate)
            for index, row in enumerate(rows):
                employee_id = row.get("employee_id")
                if not isinstance(employee_id, str) or not employee_id.strip():
                    quarantine(name, index, f"ROW-{name}-{index + 1}", "invalid_source_key", "Missing or invalid employee identifier.")
                    continue
                if employee_id in duplicates:
                    rejected.add((name, index))
                    continue
                if name == "employees":
                    if not valid_employee(row):
                        quarantine(name, index, employee_id, "invalid_employee", "Employee contract requires name, department, active flag and nonnegative integer salary/rate.")
                else:
                    amount_key = "gross_cents" if name == "payroll" else "overtime_minutes"
                    amount = row.get(amount_key)
                    if row.get("period") != period:
                        quarantine(name, index, employee_id, "period_mismatch", f"{name} record belongs to a different month.")
                    elif not _integer(amount) or not 0 <= amount <= 100_000_000:
                        quarantine(name, index, employee_id, "invalid_amount", f"{amount_key} must be a nonnegative integer within the source contract limit.")
                    elif employee_id not in raw_roster:
                        quarantine(name, index, employee_id, "unknown_employee", f"{name} references an employee absent from the HR roster.")

        valid = {name: [row for index, row in enumerate(rows) if (name, index) not in rejected]
                 for name, rows in sources.items()}
        roster_by_id = {row["employee_id"]: row for row in valid["employees"]}
        attendance_ids = {row["employee_id"] for row in valid["attendance"]}
        payroll_ids = {row["employee_id"] for row in valid["payroll"]}
        for employee_id, employee in roster_by_id.items():
            if employee["active"]:
                for name, available in (("attendance", attendance_ids), ("payroll", payroll_ids)):
                    if employee_id not in available and employee_id not in invalid_keys[name]:
                        finding(employee_id, f"missing_{name}", "critical", f"Active employee is missing a {name} record.",
                                "Time & Attendance" if name == "attendance" else "Payroll",
                                f"Restore the {name} record for {period} and rerun reconciliation.")
            elif employee_id in payroll_ids:
                amount = next(row["gross_cents"] for row in valid["payroll"] if row["employee_id"] == employee_id)
                if amount > 0:
                    finding(employee_id, "inactive_employee_payment", "critical", "An inactive employee has reported gross pay.",
                            "HR Operations + Payroll", "Confirm employment dates or reverse the source payment, then rerun.", amount)
                    invalid_keys["payroll"].add(employee_id)
                    for index, row in enumerate(sources["payroll"]):
                        if row.get("employee_id") == employee_id:
                            rejected.add(("payroll", index))

        # Real Pandas ETL: enforce one-to-one joins, compute finance-safe values,
        # and aggregate the resulting department facts for the business dashboard.
        employee_columns = ["employee_id", "employee_name", "department", "active", "base_salary_cents", "hourly_cents"]
        hr = pd.DataFrame(valid["employees"], columns=employee_columns)
        attendance = pd.DataFrame(valid["attendance"], columns=["employee_id", "overtime_minutes"])
        payroll = pd.DataFrame([row for row in valid["payroll"] if row["employee_id"] not in invalid_keys["payroll"]],
                               columns=["employee_id", "gross_cents"])
        merged = hr.merge(attendance, on="employee_id", validate="one_to_one").merge(payroll, on="employee_id", validate="one_to_one")
        records = []
        for row in merged.to_dict("records"):
            expected = expected_gross(int(row["base_salary_cents"]), int(row["hourly_cents"]), int(row["overtime_minutes"])) if row["active"] else 0
            gross = int(row["gross_cents"])
            variance = gross - expected
            if abs(variance) > 10_000:
                finding(row["employee_id"], "gross_difference", "critical", "Reported gross differs from the simplified expected amount by more than ₪100.",
                        "Payroll", "Verify salary and overtime inputs with HR; reconcile the source gross amount and rerun.", variance)
            if row["overtime_minutes"] > 1_800:
                finding(row["employee_id"], "overtime_threshold", "warning", "Overtime exceeds the review threshold of 30 hours.",
                        "Department Manager", "Review and document authorization for this employee's overtime.")
            employee_findings = [f for f in findings if f["employee_id"] == row["employee_id"]]
            status = "critical" if any(f["severity"] == "critical" for f in employee_findings) else "warning" if employee_findings else "matched"
            records.append({"employee_id": row["employee_id"], "employee_name": row["employee_name"], "department": row["department"],
                            "expected_cents": expected, "gross_cents": gross, "variance_cents": variance,
                            "overtime_minutes": int(row["overtime_minutes"]), "status": status})
        facts = pd.DataFrame(records, columns=["employee_id", "department", "gross_cents", "expected_cents", "variance_cents", "overtime_minutes"])
        departments = []
        if not facts.empty:
            summary = facts.groupby("department", sort=True).agg(headcount=("employee_id", "count"),
                          gross_cents=("gross_cents", "sum"), variance_cents=("variance_cents", "sum")).reset_index()
            departments = [{"name": row["department"], "headcount": int(row["headcount"]),
                            "gross_cents": int(row["gross_cents"]), "variance_cents": int(row["variance_cents"])}
                           for row in summary.to_dict("records")]
        affected = {f["employee_id"] for f in findings if f["employee_id"] in raw_roster}
        input_count = sum(len(rows) for rows in sources.values())
        return {"period": period, "rule_version": RULE_VERSION, "metrics": {
                    "headcount": len(records), "gross_cents": sum(r["gross_cents"] for r in records),
                    "expected_cents": sum(r["expected_cents"] for r in records), "variance_cents": sum(r["variance_cents"] for r in records),
                    "overtime_minutes": sum(r["overtime_minutes"] for r in records),
                    "quality_score": round(100 * (len(raw_roster) - len(affected)) / max(len(raw_roster), 1), 1),
                    "published_run_id": None},
                "departments": departments, "findings": findings, "records": records,
                "accepted_rows": input_count - len(rejected), "rejected_rows": len(rejected),
                "sources": [{"name": name,
                             "rows": len(rows), "status": "quarantined" if any(key[0] == name for key in rejected) else "validated"}
                            for name, rows in sources.items()]}
