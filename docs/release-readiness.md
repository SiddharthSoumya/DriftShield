# Release readiness and trust boundaries

The initial hardening review examined the merged infrastructure release at `71c771f`. The hardening branch adds native intake schema checks, duplicate delivery consistency, accepted-observation rate limits, credential separation and bounded provider output, more conservative Terraform evidence labels, proxy safeguards, and a dependency audit in CI.

## Customer-hosted release gates

| Gate | Acceptance evidence |
| --- | --- |
| Trusted deployment | Deploy from a reviewed commit. Confirm only the HTTPS proxy is externally reachable. Check request limits, application credentials, and sign-in behavior on the actual host. |
| Real collection | Use the customer's staging AWS account with the documented read role. Check inventory and one controlled supported change per sold provider. Confirm wrong account or a denied API call produces a failure. |
| Evidence | Verify a baseline, controlled change, review note, and recovery. Confirm Terraform shows reconciliation and that independent provider evidence proves a requested rollback. |
| Operational alerting | Send a real SMTP test and check the mailbox. Monitor worker health externally, because a stopped worker cannot deliver its own warning. Confirm an overdue target is detected. |
| Disaster recovery | Restore PostgreSQL and the protected service configuration to a new host, test sign-in and collector ingestion, and measure recovery time and data loss. Document the supported recovery objectives. |
| Dependencies and reporting | Keep CI dependency checks green and confirm a private security reporting channel and patch process. |

## Remaining product and security work

- The ingestion token is a bearer credential. It authorizes a single target, but neither the credential nor a content hash independently proves origin or truth. Use dedicated trusted runners, scoped provider identities, and independent checks for high-impact changes. Evaluate short-lived identity and independently verifiable source and scope before offering strong provenance guarantees.
- Collection is sampled and covers a defined set of settings. Expected scaling may create findings, and a short-lived change between scans can go unseen. Add per-field policy and exceptions for autoscaling and add event-based provider sources for changes where timing or attribution matters. Verify completeness per provider before treating an observation as evidence that a resource was removed.
- The database and web process are shared administrative boundaries. Application membership checks and tests are in place. Add MFA or SSO, independent tenant-isolation testing, and a database-level boundary before operating a shared public SaaS for unrelated companies.
- The audit and evidence records can be modified by an operator with database access. Add an external append-only export and immutable retention if customers require independently verifiable records.
- Cost alerts identify configuration drivers. Customer bills, spend limits and cost attribution need provider billing data and remain outside coverage. Monitor actual spend through the provider's budget tools.
