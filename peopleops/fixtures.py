"""Deterministic, fictional HR, attendance and payroll source snapshots."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .rules import expected_gross

PERIODS = ("2026-07", "2026-08", "2026-09")
SCENARIOS = ("clean", "review", "corrected")
DEPARTMENTS = ("Human Resources", "Finance", "Claims", "Customer Service", "Technology", "Operations")


def employee_fixture() -> list[dict[str, Any]]:
    """Return 72 fictional employees with no real names or identifiers."""
    employees = []
    for number in range(1, 73):
        base = (9_000 + (number * 731) % 16_000) * 100
        hourly = int((Decimal(base) / Decimal(186)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        employees.append({
            "employee_id": f"EMP-{number:03}", "employee_name": f"Demo Employee {number:03}",
            "department": DEPARTMENTS[(number - 1) % 6], "active": True,
            "base_salary_cents": base, "hourly_cents": hourly,
        })
    return employees


def source_fixture(period: str, scenario: str = "review", employees: list[dict] | None = None) -> dict[str, list[dict]]:
    """Build source files; an optional employee roster replaces only the HR source."""
    if scenario not in SCENARIOS:
        raise ValueError(f"scenario must be one of {', '.join(SCENARIOS)}")
    roster = employee_fixture()
    month_offset = int(period[-2:]) - 7
    attendance, payroll = [], []
    for number, employee in enumerate(roster, start=1):
        minutes = ((number * 7 + month_offset * 3) % 19) * 30
        if scenario == "corrected" and number == 33:
            minutes = 660
        attendance.append({"employee_id": employee["employee_id"], "period": period, "overtime_minutes": minutes})
        payroll.append({"employee_id": employee["employee_id"], "period": period,
                        "gross_cents": expected_gross(employee["base_salary_cents"], employee["hourly_cents"], minutes)})
    if scenario == "review":
        payroll.append(deepcopy(payroll[3]))
        attendance.append({"employee_id": "EMP-999", "period": period, "overtime_minutes": 60})
        payroll[9]["gross_cents"] = -25_000
        attendance = [row for row in attendance if row["employee_id"] != "EMP-018"]
        payroll = [row for row in payroll if row["employee_id"] != "EMP-029"]
        payroll[6]["gross_cents"] += 186_500
        overtime_row = next(row for row in attendance if row["employee_id"] == "EMP-033")
        overtime_row["overtime_minutes"] = 2_220
        payroll_row = next(row for row in payroll if row["employee_id"] == "EMP-033")
        payroll_row["gross_cents"] = expected_gross(roster[32]["base_salary_cents"], roster[32]["hourly_cents"], 2_220)
        roster[71]["active"] = False
    return {"employees": deepcopy(employees if employees is not None else roster), "attendance": attendance, "payroll": payroll}


# Explicit aliases make the mock API convenient without coupling it to the pipeline.
get_employees = employee_fixture
generate_employees = employee_fixture
