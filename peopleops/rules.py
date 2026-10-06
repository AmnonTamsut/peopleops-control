"""Explicit domain contracts shared by extraction and reconciliation."""
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

MAX_SOURCE_AMOUNT = 100_000_000


def expected_gross(base_cents: int, hourly_cents: int, overtime_minutes: int) -> int:
    """Simplified portfolio calculation, not statutory payroll."""
    overtime = Decimal(overtime_minutes) * Decimal(hourly_cents) * Decimal('1.25') / Decimal(60)
    return base_cents + int(overtime.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def valid_employee(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    text_fields = ('employee_id', 'employee_name', 'department')
    return (all(isinstance(item.get(field), str) and item[field].strip() for field in text_fields)
            and type(item.get('active')) is bool
            and all(type(item.get(field)) is int and 0 <= item[field] <= MAX_SOURCE_AMOUNT
                    for field in ('base_salary_cents', 'hourly_cents')))
