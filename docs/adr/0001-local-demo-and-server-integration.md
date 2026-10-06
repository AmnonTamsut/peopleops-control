# ADR 1: Runnable local demo and explicit SQL Server integration

Use SQLite for the local control room so a reviewer can run the project without credentials or database infrastructure. Keep the SQL Server schema, queries and Python publishing adapter separate and explicit. Do not imply that SQLite validates T-SQL concurrency semantics or Microsoft driver behavior.

Runs are immutable evidence. Monthly publication is a mutable pointer updated only after critical checks pass. This preserves the last valid data while a failed attempted import remains inspectable. Monetary inputs use integer agorot and Decimal rounding. Synthetic source snapshots make demonstrations reproducible without personal data.
