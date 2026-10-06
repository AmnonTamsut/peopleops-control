# Operations runbook

## Local execution
From the repository root, run `python -m peopleops run --period 2026-09 --scenario clean`. Exit 0 means critical checks passed; exit 2 means the pipeline completed with critical findings and publication was blocked. Unexpected extraction or runtime errors fail the command and must be investigated. Capture stdout/stderr in the scheduler's protected log location.

## Scheduling
Example Linux cron, on the second day of each month at 06:00 Israel time:

```cron
CRON_TZ=Asia/Jerusalem
0 6 2 * * cd /srv/peopleops-control && .venv/bin/python -m peopleops run --period "$(date -d 'last month' +\%Y-\%m)" --scenario clean >> /var/log/peopleops.log 2>&1
```

GNU `date` and a cron implementation supporting `CRON_TZ` are assumed. Container/Kubernetes cron can call the same CLI with an explicit business period. Windows Task Scheduler can run `.venv\Scripts\python.exe -m peopleops run --period 2026-09 --scenario clean` with the repository as the start directory; calculate the previous month in its wrapper script. No recurring task is installed by this project.

Fixtures are the default inputs. An API adapter is available via `--hr-url`; payroll and attendance remain fixture sources. Real attendance/payroll ingestion and secret management need their own source contracts before use with actual employees.

## Recovery
1. Look up the attempted run in history and download its immutable review pack.
2. Route critical findings to the displayed source owner. Fix the source, never mutate an old run's evidence.
3. Run corrected inputs; their checksum creates a new run. The demonstration uses `--scenario corrected`.
4. An identical rerun reuses the existing evidence. Inspect `reused` to distinguish a replay from new processing.
5. Publication remains pointed at the last clean run when critical findings occur. A passing corrected run atomically replaces that pointer.

HR HTTP extraction retries transient transport/429/5xx errors with bounded backoff; authentication errors fail immediately. Extraction returns either a complete validated employee snapshot or an error. No partial API snapshot enters the pipeline.

## Evidence, backup and limits
Keep the SQLite database and its generated source snapshots together. Copy the database using SQLite's backup API while running, or stop the service before copying files. The local single-writer lock is appropriate for this demo; SQL Server provides the separate transactional publication path for database concurrency. The API is a local demo without authentication: bind to localhost and use synthetic data. Production adoption requires authenticated access, authorized exports, centrally managed scheduling/alerting, retention policies, real source contracts and SQL Server integration validation.
