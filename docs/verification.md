# Verification and review

## Automated checks

The user approved the pipeline service, HTTP API/HR extraction and Excel export boundaries. `python -m unittest discover -s tests -v` ran **43 tests in 2.632 seconds: all passed**. Full output is included in the downloadable archive at `artifacts/test-results.txt`.

| Boundary | Tests | Coverage |
|---|---:|---|
| Pipeline service | 17 | Independent financial examples, half-up rounding, both signs of the ILS 100 tolerance boundary, quarantine, immutability, corrections, month isolation and snapshot tampering |
| Concurrent service callers | 1 | Simultaneous identical imports create one published run, with one caller reusing it |
| HTTP API | 11 | Real ASGI requests, publication gating, historical reports, idempotency, validation/404s, pagination, static assets and seed lifecycle |
| HR HTTP extraction | 9 | Transient retries, limits, authentication failure, malformed pages/JSON, invalid employee fields, repeated IDs and safe errors |
| Excel exports | 5 | Independent financial cells, filters/formatting, owners/remediation, file checksums, historical exports and eight formula-injection variants |

API tests use HTTPX's ASGI transport with real route handling, validation, ETL and SQLite storage. The external HR service is simulated at its HTTP transport boundary. No internal pipeline/database behavior is mocked. A test-only async timer keeps the event loop responsive because this sandbox misses some worker-thread wakeups; requests have five-second timeouts. No production behavior was changed for that harness.

The API tests uncovered a real defect: GET `/api/dashboard?period=0000-01` raised an unhandled error even though POST correctly rejected that period. A failing regression test reproduced it; the dashboard now translates service validation errors into HTTP 422, and the regression passes. Other tests characterize existing public behavior with independently worked financial answers.

Python compilation, JavaScript syntax and `git diff --check` passed. CI now installs dependencies, runs the suite and checks syntax; execution on GitHub remains unverified.

## Earlier manual checks

Manual checks ran in a fresh temporary data directory after the implementation and review fixes:

| Behavior | Observed result |
|---|---|
| Independent financial worked example | Base 1,000,000 agorot + 120 minutes at 5,000 agorot/hour produced 1,012,500 agorot |
| Half-up rounding | Base 1,000 + 30 minutes at 4 agorot/hour produced 1,003 agorot |
| Clean import | Published, 72 reconciled employees |
| Faulty review import | Blocked, seven critical findings, one warning, 67 reconciled employees |
| Last valid publication | Review attempt retained the clean run's publication pointer |
| Corrected inputs | New published run; repeating it returned reused=true, with three total runs across clean/review/corrected |
| Workbook | Summary, Reconciliation, Exceptions, Lineage; 67 fact rows and eight exception rows |
| Excel money formatting | Headcount remains a number; gross/expected/variance use ILS formatting |
| Spreadsheet formula input | A source name beginning =HYPERLINK was exported as quoted string text, never a formula |
| API handler checks | Health, dashboard and report route handlers executed in process; invalid month rejected by request model |
| HR transient failure | One 503 retried, then three pages returned all 72 employees |
| HR whitespace identifier | Rejected by shared employee contract |
| Source lineage | Actual JSON/CSV snapshots retained with SHA256 hashes and relative paths |
| Syntax | Python compileall and node --check passed |

These manual checks supplement the automated suite. Browser interactions, live socket serving and SQL Server/ODBC execution remain unverified in this environment. Windows locking is implemented but was not executed on Windows. ASGI tests verify HTTP application behavior without proving deployment networking or browser interaction.

## Matt Pocock two-axis review

The requested skills were read from Matt Pocock's public repository: [ask-matt](https://github.com/mattpocock/skills/blob/main/skills/engineering/ask-matt/SKILL.md), [implement](https://github.com/mattpocock/skills/blob/main/skills/engineering/implement/SKILL.md), [tdd](https://github.com/mattpocock/skills/blob/main/skills/engineering/tdd/SKILL.md), [code-review](https://github.com/mattpocock/skills/blob/main/skills/engineering/code-review/SKILL.md). They were not installed as a plugin. Ask-matt routes workflow choices; it is not a consultation with Matt personally.

Review baseline: initial empty commit `941e4581a3a0190da755b169fb2985e604bad2e2`. Two independent review agents inspected implementation commit `0770434`.

### Standards

No blocking money, SQL parameterization, bounded-retry or audit-preservation violation found. Two judgement findings were addressed: employee validation was centralized to remove whitespace-ID contract drift, and the financial business rule moved out of fixtures into `peopleops/rules.py`.

### Spec

The run button could display the latest-created run instead of the older run returned by a reused request. Fixed by rendering the POST evidence with refreshed run history and labeling metrics as the selected run. No scope creep found. The previously missing automated tests are now delivered, and CI runs them.

### Test-delivery review

Two separate Standards/Spec agents reviewed commit `9113730` against the previous delivered commit `170cb8a`. Neither found a blocking correctness or data-safety issue. The Standards review requested type annotations on test methods/helpers; those were added. The Spec review confirmed approved public boundaries, independent monetary examples, accurate test counts and honest execution limits, and flagged the previously generated ZIP as stale. The release archive was rebuilt from the final tracked source, including all five test files and the complete test-results log.
