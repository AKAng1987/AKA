# CGI Lambda IAM Audit — 2026-09-12

**Scope:** all 19 `cmon-stage-backend-*` Lambdas in ap-southeast-1.
**Status:** read-only audit. NO changes applied. User reviews before any tightening.

---

## Two role patterns in production

### Pattern A: Shared over-broad role (16 Lambdas)

`cmon-stage-backend-Stack-ServiceRole-L7OPOC60JECE`

Attached: XRayWriteOnly, LambdaVPCAccess, SSMReadOnly, LambdaBasicExecution, LambdaRole, KmsPolicy.

Inline (`cmon-stage-backend-ServiceRolePolicies`):

- `dynamodb:*` on 9 tables (price-history, weekly-metrics, gdp-history, inflation-history, derived_metrics, metrics-source, fed-calendar, fed-calendar/index/*, model-history)
- `s3:GetObject/PutObject/DeleteObject` on `cmon-stage-backend-*-reports/*`
- `SNS:Publish` on `cmon-stage-backend-model-update`

Lambdas using it: alpha-vantage-updater, clock-model-updater, compass-model-updater, crypto-price-updater, dashboard, derived-metrics-updater, fred-data-updater, gdp-updater, grid-model-updater, inflation-rate-updater, model-report, mtd-report, price-updater, router, us-yield-updater, yahoo-finance-updater.

### Pattern B: Per-Lambda least-privilege (3 Lambdas, newer)

- `backtest-refresher-role`: Query on 2 tables, PutObject scoped to `Cache/backtest/*` only
- `regime-signal-updater-role`: Query on 2 tables, PutItem on 1 table
- `regime-outcome-backfill-role`: Query on 2 tables, Scan+UpdateItem on 1 table

All three include scoped log-group grants.

---

## Findings

### F1 — `dynamodb:*` includes write actions on read-only Lambdas
`dashboard`, `model-report`, `mtd-report`, `router` are read paths. They currently have DeleteItem / DeleteTable / UpdateTable authority on all 9 tables. Blast radius of a compromised deployment: could truncate `price-history` (24k+ SPX rows) or `model-history` before detection.

### F2 — Provider updaters have write access to unrelated tables
`alpha-vantage-updater`, `fred-data-updater`, `us-yield-updater`, `yahoo-finance-updater`, `crypto-price-updater`, `price-updater` all write price-history (and a few read metrics-source). None need write access to model-history, gdp-history, inflation-history, weekly-metrics, derived_metrics, or fed-calendar. Currently all have full write on all.

### F3 — Model updaters have write access to price-history
`compass-model-updater`, `grid-model-updater`, `clock-model-updater` write to `model-history` only. Currently have write on price-history and every other table.

### F4 — `s3:DeleteObject` on entire reports bucket
No known Lambda deliberately deletes S3 objects in the reports bucket. The shared role has `s3:DeleteObject` on `*`. A compromised Lambda could wipe cached workbooks and cache blobs.

### F5 — `SSMReadOnly` attached to every shared-role Lambda
No CGI Lambda reads SSM Parameter Store (confirmed earlier: no Secrets Manager / SSM in use). Remove the AWS-managed policy attachment.

### F6 — `AWSLambdaRole` attached (allows invoking other Lambdas)
Grants `lambda:InvokeFunction` on `*`. Only `router` actually orchestrates other Lambdas. 15 of 16 shared-role Lambdas don't need this.

### F7 — `AWSLambdaVPCAccessExecutionRole` attached but no VPC config
None of the CGI Lambdas run in a VPC (verified via prior work). Remove the attachment.

---

## Proposed remediation (phased, NOT executed)

**Phase 1 — Zero-risk trims (managed policies):** detach `SSMReadOnly`, `AWSLambdaVPCAccessExecutionRole`, `AWSLambdaRole` from the shared role. Keep `router` on a separate role that keeps `AWSLambdaRole`. Effect: shrinks blast radius, no functional change.

**Phase 2 — Split shared role by function class:**

| New role | Members | Access |
|---|---|---|
| `cmon-stage-backend-price-provider-role` | alpha-vantage / crypto-price / fred-data / price / us-yield / yahoo-finance / derived-metrics updaters | Read metrics-source; PutItem on price-history + derived_metrics |
| `cmon-stage-backend-macro-provider-role` | gdp / inflation-rate updaters | Read metrics-source; PutItem on gdp-history + inflation-history |
| `cmon-stage-backend-model-updater-role` | compass / grid / clock model updaters | Read price-history + derived_metrics + metrics-source; PutItem on model-history; SNS:Publish |
| `cmon-stage-backend-readpath-role` | dashboard / model-report / mtd-report | Query on all 9 tables (no writes); S3 GetObject + PutObject on reports (dashboard writes the HUD workbook) |
| `cmon-stage-backend-router-role` | router | Read metrics-source; lambda:InvokeFunction scoped to CGI Lambda ARNs |

Effect: any single compromised Lambda can no longer wreck tables it doesn't own.

**Phase 3 — S3 tightening:** replace bucket-wide `DeleteObject` with `PutObject/GetObject` only. If any Lambda genuinely needs Delete (dashboard cleanup?), scope by prefix.

---

## Risk of applying blindly

**Do NOT bulk-apply.** Each Lambda's actual DynamoDB access pattern must be verified in code (`aws_service_helpers/dynamo_wrapper.py` or per-Lambda handler) before tightening — a Lambda that silently uses BatchWriteItem or TransactWriteItem would fail after tightening to `PutItem`-only. The correct sequence per Lambda:

1. Read handler code, list every DynamoDB action used
2. Publish new version + point `live` alias at it
3. Deploy new role attached
4. Watch CloudWatch for AccessDenied for 24-48h
5. If clean, remove old role association; if AccessDenied, rollback alias

Total effort for full sweep: ~4-6 hours across sessions.

---

## Recommendation

Start with **Phase 1** (managed-policy detachments). Zero risk, immediate blast-radius reduction. ~15 min.
Defer Phase 2 (role splits) until after Markov Phase 2 build — it's a bigger undertaking and the current shared role isn't leaking, just wide.
