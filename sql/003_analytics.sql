/* Parameterize @Period from the caller. In SSMS, replace the sample month. */
DECLARE @Period char(7) = '2026-09';

/* Preaggregate findings before joining to facts to avoid multiplying amounts. */
WITH EmployeeFindings AS (
    SELECT RunId, EmployeeId,
           SUM(CASE WHEN Severity = 'warning' THEN 1 ELSE 0 END) AS WarningCount,
           SUM(CASE WHEN Severity = 'critical' THEN 1 ELSE 0 END) AS CriticalCount
    FROM dbo.QualityFinding
    WHERE RunId = (SELECT RunId FROM dbo.MonthlyPublication WHERE Period = @Period)
    GROUP BY RunId, EmployeeId
), DepartmentTotals AS (
    SELECT f.Department, COUNT(*) AS Headcount,
           SUM(f.GrossCents) AS GrossCents,
           SUM(f.ExpectedCents) AS ExpectedCents,
           SUM(f.VarianceCents) AS VarianceCents,
           SUM(CAST(f.OvertimeMinutes AS bigint)) AS OvertimeMinutes,
           SUM(COALESCE(q.WarningCount, 0)) AS WarningCount
    FROM dbo.MonthlyPublication p
    JOIN dbo.PayrollFact f ON f.RunId = p.RunId
    LEFT JOIN EmployeeFindings q ON q.RunId = f.RunId AND q.EmployeeId = f.EmployeeId
    WHERE p.Period = @Period
    GROUP BY f.Department
)
SELECT Department, Headcount,
       CAST(GrossCents / 100.0 AS decimal(19, 2)) AS GrossILS,
       CAST(ExpectedCents / 100.0 AS decimal(19, 2)) AS ExpectedILS,
       CAST(VarianceCents / 100.0 AS decimal(19, 2)) AS VarianceILS,
       OvertimeMinutes, WarningCount,
       DENSE_RANK() OVER (ORDER BY ABS(VarianceCents) DESC) AS VarianceRank,
       CAST(100.0 * GrossCents / NULLIF(SUM(GrossCents) OVER (), 0) AS decimal(9, 2)) AS PayrollSharePct
FROM DepartmentTotals
ORDER BY VarianceRank, Department;

/* Month-to-month movement uses only the active immutable publication per month.
   LAG compares the preceding available month; the gap flag avoids claiming that
   a missing month represents consecutive monthly movement. */
WITH MonthlyDepartments AS (
    SELECT p.Period, f.Department, SUM(f.GrossCents) AS GrossCents, COUNT(*) AS Headcount
    FROM dbo.MonthlyPublication p
    JOIN dbo.PayrollFact f ON f.RunId = p.RunId
    WHERE p.Period <= @Period
    GROUP BY p.Period, f.Department
), Movement AS (
    SELECT *, LAG(GrossCents) OVER (PARTITION BY Department ORDER BY Period) AS PreviousGrossCents,
              LAG(Period) OVER (PARTITION BY Department ORDER BY Period) AS PreviousPeriod
    FROM MonthlyDepartments
)
SELECT Period, Department, Headcount, GrossCents, PreviousPeriod,
       CASE WHEN DATEDIFF(month, CONVERT(date, PreviousPeriod + '-01'), CONVERT(date, Period + '-01')) = 1
            THEN GrossCents - PreviousGrossCents END AS ConsecutiveMonthChangeCents
FROM Movement WHERE Period = @Period ORDER BY Department;
