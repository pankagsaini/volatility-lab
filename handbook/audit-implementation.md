# Repository review: implementation stages

Review supplied 13 November 2026. Findings were checked against the current repository.

| Finding | Evidence and disposition |
| --- | --- |
| Root implementation monolith | Confirmed: stage 1 moves 83 modules to owning packages with identity-preserving aliases. |
| Multiple startup implementations | Confirmed: one packaged bootstrap/launcher replaces duplicate setup. |
| Broad test discovery | Confirmed: tests are classified under tests/. Architecture/startup tests are added; CI enforces 35% combined statement/branch coverage. |
| Missing retries/rate limiting | Partly incorrect: broker retries and cross-process pacing exist. Shared read retry/circuit handling and redacted events are now implemented. |
| Missing provenance/stale guards | Partly incorrect: quote gates, input hashes and IV provenance exist. Reference checksums, versioned replay receipts and legacy modeled-IV quarantine now cover the remaining confirmed gaps. |
| Scattered configuration | Fixed through a validated settings layer and an explicit optional env-file path. |
| Generated data mixed with source | Fixed for new installations through per-profile runtime stores. Recognized legacy stores remain supported. |
| Business logic in UI | Partly confirmed: core pricing/backtesting already have independent modules. New calculations belong in these modules/services; browser calculations have direct Node tests. |
| Unclear trust model | Documentation gap: endpoint allowlisting already exists. Trust boundaries now documented. |
| Dependency/documentation drift | Fixed with a hashed lockfile, dependency auditing, a maintained Python matrix and a documentation index. |

1. **Foundations:** actual package boundaries, canonical startup, organized tests and accurate docs.
2. **Operations:** central validated settings, read-only health, redacted structured events and resilient external reads.
3. **Validation/data:** dependency/security checks, property tests, coverage gate and safe runtime/artifact separation.

Each stage must pass full CI and retain browser pricing/provenance tests. Structural cleanup must not change pricing policy, fabricate IV or rewrite historical captures.

## Operational changes and validation

The desk and local service use validated environment settings. Profiles have separate stores by default. The health CLI reports SQLite readability, archive sync and saved snapshot age. Broker, official-source and cloud snapshot reads now have bounded retries, backoff and a process-local circuit breaker. The archive importer supplies the seed utility with verified records. Bundled reference inputs have a checksummed versioned manifest.

CI uses a checked dependency lock, audits resolved packages, enforces a 35% package-wide coverage floor and tests numerical properties. Archived 3D surfaces use recorded broker IV. Interpolation is a display calculation restricted to the observed strike range. See the [operator runbook](operations.md).

The package supports Python 3.11 and newer. Python 3.10 dependency resolution selected vulnerable releases whose available fixes require Python 3.11. CI exercises Python 3.11, 3.12 and 3.13. The bundled snapshot was reconciled against its original private Upstox archive, and each row records the archive filename and hash.
