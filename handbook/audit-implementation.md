# Repository review: implementation stages

Review supplied 13 November 2026. Findings were checked against the current repository.

| Finding | Evidence and disposition |
| --- | --- |
| Root implementation monolith | Confirmed: stage 1 moves 83 modules to owning packages with identity-preserving aliases. |
| Multiple startup implementations | Confirmed: one packaged bootstrap/launcher replaces duplicate setup. |
| Broad test discovery | Confirmed: tests are classified under tests/. Architecture/startup tests are added; coverage is measured before setting a defensible gate. |
| Missing retries/rate limiting | Partly incorrect: broker retries and cross-process pacing exist. Circuit breaking and consistent events remain useful. |
| Missing provenance/stale guards | Partly incorrect: quote gates, input hashes and IV provenance exist. Artifact checks remain to standardize. |
| Scattered configuration | Confirmed: the environment template advertises unused variables; README implied automatic .env loading. |
| Generated data mixed with source | Confirmed: runtime writers still target package data/. Relocation must preserve captures. |
| Business logic in UI | Partly confirmed: core pricing/backtesting already have independent modules. New calculations belong in these modules/services; browser calculations have direct Node tests. |
| Unclear trust model | Documentation gap: endpoint allowlisting already exists. Trust boundaries now documented. |
| Dependency/documentation drift | Confirmed: lower-bound-only installs need a reproducible workflow. README paths, test counts and Pages instructions were stale. |

1. **Foundations:** actual package boundaries, canonical startup, organized tests and accurate docs.
2. **Operations:** central validated settings, read-only health, redacted structured events and resilient external reads.
3. **Validation/data:** dependency/security checks, property tests, coverage gate and safe runtime/artifact separation.

Each stage must pass full CI and retain browser pricing/provenance tests. Structural cleanup must not change pricing policy, fabricate IV or rewrite historical captures.

## Operational changes and validation

The desk and local service use validated environment settings. Profiles have separate stores by default. The health CLI reports SQLite readability, archive sync and saved snapshot age. Broker, official-source and cloud snapshot reads now have bounded retries, backoff and a process-local circuit breaker. The archive importer supplies the seed utility with verified records. Bundled reference inputs have a checksummed versioned manifest.

CI uses a checked dependency lock, audits resolved packages, enforces the 31% package-wide baseline and tests numerical properties. Archived 3D surfaces use recorded broker IV. Interpolation is a display calculation restricted to the observed strike range. See the [operator runbook](operations.md).
