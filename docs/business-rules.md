# Reconciliation rules and source contract

This is a control report, not a payroll engine. It cannot calculate Israeli tax, benefits, pension contributions, holidays, collective agreements, absence deductions or legally correct overtime. The synthetic worked examples intentionally keep payroll simple so discrepancies have an independent expected value.

## Worked example
Base gross is ILS 10,000; the explicit hourly rate is ILS 50; overtime is 120 minutes. Expected gross is 10,000 + (120 / 60 × 50 × 1.25) = **ILS 10,125**. Reported ILS 10,325 is a positive ILS 200 difference and fails the ILS 100 control tolerance. The tolerance is a demonstration assumption, not Harel policy.

Money enters as integer agorot (100 agorot = ILS 1). Expected overtime uses Decimal and rounds once, half up, to one agora. The synthetic monthly salary divided by 186 is only a fixture convention for deriving the explicit hourly rate. Source adapters must supply the rate appropriate to their business contract.

## Source fields

| Source | Key | Required fields | Owner |
|---|---|---|---|
| HR | employee_id | employee_name, department, base_salary_cents, hourly_cents, active | HR Operations |
| Attendance | employee_id, period | overtime_minutes | Time & Attendance |
| Payroll | employee_id, period | gross_cents | Payroll |

IDs are strings. Amounts and minutes are nonnegative integers; `active` is boolean. Month format is YYYY-MM. The sample employees have invented names and IDs, no national IDs, bank data, addresses or contacts.

Duplicate keys, invalid values, unknown IDs and missing links must be visible as findings, never silently joined into double payments. Structurally invalid rows are quarantined. Critical findings block the publication pointer. Overtime above 30 hours is a warning. A gross difference strictly greater than ILS 100 is critical. The reported gross and expected gross are evidence about the latest attempt; they do not imply approval to pay.
