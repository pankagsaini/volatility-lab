# Private AWS collector

Status: prepared locally, not deployed or connected to the dashboard yet. On 16 October 2026 AWS CloudShell refused environment creation: "Your account verification is in progress. This may take up to two days for new accounts." No collector stack, roles, schedules or token parameter have been created.

User approval received on 16 October 2026 for storing the existing read-only Upstox Analytics Token as encrypted SSM SecureString in AWS account 562402926566, and creating the two restricted collector/scheduler roles. Do not ask for this same approval again. Paid upgrades and public access are not authorised.

Verified locally: five unit tests passed. Live broker catalogue returned three futures and seven option expiries for NIFTY, three futures and three option expiries for BANKNIFTY; six futures quotes returned, plus 104 rows for the nearest NIFTY option chain. This verifies local broker access, not AWS egress or deployment.

Account: AWS Free Plan, observed 16 October 2026. Console shows $100 credit and expiry 16 April 2027. Never upgrade this account or activate paid-only services. Credits can expire earlier if exhausted. Free Plan suspends access on expiry, so download archives while the account is still active.

## Prepared configuration

- Region: ap-southeast-2 (the user's existing project).
- Five-minute raw captures, weekdays 09:00–16:00 Asia/Kolkata. AWS Scheduler has minute-level precision, not tick timing.
- NIFTY options through the third monthly futures expiry, including listed weeklies; BANKNIFTY monthly options; three futures per index; index and India VIX quotes.
- Daily candles: overlapping ten-day requests at 17:00, 17:30, 19:00 and 19:30 IST. Pending candles remain explicitly pending and are revisited later.
- Raw broker IV/Greeks retained unchanged. Retrieval timestamps do not assert fresh quotes; pre-open, closed-market and holiday observations need filtering at ingestion. Weekends skipped. Special weekend trading sessions are not yet scheduled.
- DynamoDB provisioned 5 read / 10 write units, no autoscaling, private table, encrypted storage. Compressed chunks with checksum and manifest-last publication.
- Lambda 512 MB, 240-second timeout, one concurrent invocation. Bounded network retries. Seven-day technical logs, without tokens or raw broker errors.
- No public website/API, no trading permissions, no virtual machine or NAT gateway.
- Stop date: 9 April 2027, before Free Plan expiry. This stop date does not prevent an earlier credit-related suspension.

## Deployment gate

1. User approves creating the two restricted IAM roles and storing their Upstox Analytics Token in this AWS account.
2. Store token as **Standard SecureString** at `/volatility-lab/upstox-analytics` in the same region, using the AWS-managed SSM key. Never put it in code, CloudFormation parameters, logs, or chat.
3. Deploy `cloudformation.json` as `volatility-lab`, with ScheduleState DISABLED. TokenParameter is the parameter NAME, not the token value. CloudFormation IAM acknowledgement is required because roles are created.
4. Run a snapshot test within the configured capture window. Verify broker access, table objects and complete report. Test an export and compare checksums.
5. Add archive ingestion to the local lab before claiming automatic dashboard synchronisation. Keep provenance, broker timestamps and historical day/expiry mapping intact.
6. Enable schedules only after validation. Record actual cloud runtime, compressed bytes per capture, credit usage and a verified local export. Add a visible local cloud-capture status and missing-session checks.

The Free Plan is the spending boundary, not a promise of indefinite free operation. Storage/compute may consume credits. No paid upgrade is authorised. Do not add paid notification services or public access.

## Local verification

`test_collector.py` covers chunk integrity, interrupted publication, duplicate slots, partial retry and delayed EOD candles. `build_template.py` rebuilds the credential-free template after code changes. `export_archive.py` downloads a day through an authenticated AWS SDK session, verifies chunks and writes a ZIP with its own checksums.

Outstanding: actual cloud deployment, cloud egress/token test, scheduled capture verification, failure monitoring and automatic local ingestion. These are not implemented merely by generating the template.
