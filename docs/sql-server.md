# SQL Server evidence store

This optional integration demonstrates SQL Server specifically, alongside the runnable SQLite demo. It imports synthetic PeopleOps reconciliation evidence. It neither calculates statutory Israeli payroll nor issues payments, and it is independent of Harel. SQL Server scripts have not been executed against a server in this workspace; a successful SQLite demonstration does not validate T-SQL or ODBC connectivity.

## Setup

Use SQL Server 2019 or newer and a dedicated empty database. An administrator runs these scripts in order, against that database, using SSMS or `sqlcmd`:

1. `sql/001_schema.sql` creates the tables, covering indexes and immutability triggers. Run once; it deliberately does not drop existing evidence.
2. `sql/002_publish.sql` creates or updates `dbo.PublishReviewedRun`.
3. `sql/003_analytics.sql` executes the sample analytical queries. Change the parameter month after importing data.

`GO` is a client batch separator. These files cannot be sent unchanged in one `cursor.execute` call. For example, `sqlcmd -S SERVER -d PeopleOpsPortfolio -E -b -i sql/001_schema.sql` uses integrated authentication and stops on errors. Use the same command for `002_publish.sql`. Do not put credentials on a command line.

Install Microsoft ODBC Driver 18 using the operating system's documented package method, then install the project's optional extra with `pip install -e '.[mssql]'`. The local demonstration does not require this driver or pyodbc. Set `PEOPLEOPS_MSSQL_CONNECTION_STRING` through a secret store or a private environment configuration. For a Windows integrated-authentication connection, the shape is:

```text
Driver={ODBC Driver 18 for SQL Server};Server=tcp:YOUR_SERVER,1433;Database=PeopleOpsPortfolio;Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=no;
```

Use an appropriate service identity or secrets-managed SQL authentication for your environment. The server certificate must be trusted and match the hostname. Preserve `Encrypt=yes;TrustServerCertificate=no`; install the correct CA rather than bypassing certificate verification. Never commit a connection string. The adapter does not print it or expose raw ODBC errors.

Use a restricted importer identity with SELECT and INSERT on `EtlRun`, `PayrollFact`, `QualityFinding`, `RunSource` and `RunEvidenceSeal`; SELECT on `MonthlyPublication`; and EXECUTE on `PublishReviewedRun`. Do not grant direct INSERT/UPDATE/DELETE on `MonthlyPublication`. Ownership chaining lets the same-owner procedure manage publication without granting that power directly. Application locks use the default `public` database principal. Schema installation needs a separate administrator identity. Database administrators can alter triggers and permissions; these controls are not protection against a privileged administrator.

## Python and CLI contract

```python
from pathlib import Path
from peopleops.pipeline import PeopleOpsService
from peopleops.mssql import publish_run

service = PeopleOpsService(Path(".data"))
snapshot = service.run("2026-09", scenario="clean")
result = publish_run(snapshot)
print(result)  # IDs and publication status only
```

The public function is `publish_run(run: dict, connection_string: str | None = None) -> dict`. Supply the complete result of `service.get_run(run_id)` or `service.run(...)`: top-level `run`, `metrics`, `records`, `findings`, `sources`, and `rule_version` (`simplified-gross-v1`). It returns `run_id`, `published` (boolean), `publication_status` (`published` or `blocked`), `published_run_id` (nullable), and `reused` (boolean). Different local run IDs for the same period/hash/rule resolve to the existing SQL Server run ID. A missing local run should be handled by the caller before invoking the adapter. `SQLServerPublishError` contains a safe operational message; validate the environment without printing its secrets.

The project CLI exposes `peopleops publish-sqlserver --run-id ID` for a previously generated local run. Inspect local findings before publishing; the database still independently gates critical defects. Warning-only runs may publish, matching the documented demo behavior. Critical runs are imported and retained but leave the previous monthly publication intact.

All money and duration values enter SQL as Python integers, never floats. UTF-16 length checks prevent silent truncation of Hebrew source text. All user values use ODBC parameter placeholders. A 10-second connection timeout, 30-second query timeout and 10-second lock timeout bound waits. This adapter makes one attempt and rolls back on failure; an operator can retry the same run safely.

## Transactions, evidence and concurrency

`EtlRun` has a unique `(Period, SourceHash, RuleVersion)` key. The adapter acquires a transaction-owned lock for that key before checking whether it exists, so concurrent identical imports do not race to create duplicate evidence. New run metadata, facts, findings and lineage are inserted together. A final immutable `RunEvidenceSeal` closes the run to additional evidence rows. UPDATE and DELETE triggers protect the run and its child tables; INSERT triggers protect sealed child tables.

`PublishReviewedRun` acquires a transaction-owned exclusive lock per period. It checks the evidence seal, validates record counts and financial totals, and compares persisted findings with the advertised severity counts. It checks for critical findings as well as the run outcome. Only a complete eligible run can update the single monthly publication pointer. Repeating publication of the same active run preserves `PublishedAt`. Different eligible runs for one period serialize; the last successful explicit publication request becomes active, so deliberately publishing an older reviewed run can restore that run. This is an operational choice, not automatic chronology-based selection.

The procedure can be called alone or inside the adapter's transaction. It commits only a transaction it owns and uses a savepoint when called by a transaction owner. For a doomed caller transaction, the caller must roll back; the adapter does so. Application locks release on commit or rollback. The adapter commits blocked runs as evidence and commits eligible imports together with their publication change. A failure rolls back all new evidence and the pointer change. Corrected inputs produce a new source hash and immutable run; prior evidence remains available.

## Query design and optimization

`003_analytics.sql` combines published payroll facts with findings, then ranks departments by absolute variance and calculates their share of the month's synthetic gross. Findings are aggregated by employee before the join, preventing a one-to-many join from multiplying salary totals. A second CTE/window query uses `LAG` to compare published department totals with the preceding available month, returning a delta only when months are consecutive.

The clustered primary key `(RunId, EmployeeId)` supports one-run reconciliation reads. The `(RunId, Department)` covering index supports department aggregates without loading names or the complete fact row. `(RunId, Severity)` covers quality gating and common exception summaries. A primary key on monthly period locates the publication pointer directly. Monetary divisions use exact SQL decimal literals; totals remain bigint agorot until display conversion.

For meaningful tuning, generate a larger synthetic workload and inspect the actual execution plan plus `SET STATISTICS IO, TIME ON` in a test database. Check estimated versus actual row counts, logical reads, sorts, spills and whether the covering indexes are used. Keep period filtering on the parameter rather than applying functions to indexed columns. Update statistics after bulk loads. Measure before introducing columnstore, partitioning, hints or `OPTION(RECOMPILE)`; 72 demo employees cannot justify them. This repository provides an index rationale, not a claimed benchmark.

## Server verification checklist

On a real test server, run schema/procedure setup and the analytical queries. Import a clean month, import a blocked variant and verify that publication still points to the clean run. Import a correction, retry it and verify that evidence counts and publication timestamp remain unchanged. Run two simultaneous identical imports and two eligible imports for one period; inspect the resulting audit and pointer. Try updating/deleting facts and appending to a sealed run and verify rejection. Force a failed insert and confirm complete rollback. Retain the server version, actual plans and test output as evidence before claiming SQL Server execution.
