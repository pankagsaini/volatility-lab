# Data flow and trust boundaries

Upstox quotes/candles → validated collector responses → dated archives/snapshots → service calculations → local UI or public snapshot JSON → browser.

## Inputs

Broker payloads are untrusted until schema, instrument/expiry, event time and numeric/book validity are checked. Receipt time is not quote event time. Missing/stale books cannot become execution marks. Historical gaps are separate from current snapshot freshness.

Historical IV requires broker provenance or a verified historical source. Current model inversions never fill missing historical observations. RBI/NSE inputs record URLs, retrieval time, publication dates where supplied and hashes. Index dividend yield is a proxy, not an expiry-specific forecast. Archived valuations use only inputs known by capture time.

## Boundaries

- Browser: public observations only; no broker token or privileged webhook key. Cloud paper ledger is authoritative.
- Broker client: allowlisted GET market-data endpoints; no order entry. Credentials/response bodies stay out of diagnostics.
- Apps Script / Drive: tokens in Script Properties; archive writes and triggers require owner authorization. Public reads cannot trigger collection or ledger mutations. Private webhook credentials never belong in the public site.
- Local app: validate stored observations. Health checks open databases read-only and never hydrate/repair implicitly.
- CI: fixture tests without production secrets; normal CI is read-only, deployment has narrowly scoped permissions.

## Artifacts

Bundled reference observations are dated inputs, not current data. Never fabricate timestamps or IV to improve apparent freshness. Generated caches, local databases and private archives belong outside source control. Raw captures are immutable; derived artifacts should identify captures, input hashes, version and calculation time.

Data relocation/manifest enforcement are tracked in [audit status](audit-implementation.md). The structural migration does not move or rewrite existing archives.
