# Experiment package

Run from repository root: `python -m experiments.matrix --help`.

- run.py: four workloads, command traces and per-run metadata.
- checks.py: verdicts and summary metrics.
- faults.py: verified fault injection and recovery.
- matrix.py: serial randomized schedule and acceptance checks.
- prepare.py: initialize and verify replica-set health.
- audit.py: audit new runs made by this package.
- test_consistency.py: offline regression suite.

Historical published runs must use `python scripts/audit_published.py`. See ../docs/reproduction.md and ../provenance/README.md for details.
