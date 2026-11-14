# Incident response and recovery

Assign a primary operator, backup contact, and customer communications owner before onboarding. Use an independent pager. Track time, scope, evidence, decisions, and customer impact for each incident.

| Event | First action | Recovery evidence |
| --- | --- | --- |
| Collector credential loss | Revoke the affected target token from its owner view. Preserve the runner and observation history. | Rotate after reviewing runner integrity. Confirm a fresh observation and check the last affected period independently in the provider. |
| Account or workspace access concern | Revoke membership, disable the account if appropriate, and rotate affected credentials. Review workspace actions and active sessions. | Verify permissions with independent test accounts and reconcile changes made while access was exposed. |
| Suspected GitHub App key disclosure | Revoke the key upstream and bind a new key. Check installation permissions and activity. | Verify a new read-only scan and review all installations that shared the affected App. |
| Collector overdue or scan fails | Check target health, runner scheduler, time synchronization and provider access. Keep the last good observation visible. | Confirm a new observation from the intended source before closing. |
| Database unavailable | Compare `/livez` and `/healthz`. Inspect network, service, disk, and database logs. Notify owners if monitoring is delayed. | Readiness returns 200, worker stages progress, and new observations and mail are accepted. |
| Queue or email delay | Check worker stages, SMTP relay and pending work. Verify an independently delivered alert. | Check oldest outstanding work and mailbox receipt. Expect possible duplicates after an interrupted delivery. |
| Recovery or region loss | Preserve the affected system and restore the database and protected environment file on a clean host. Follow the operations guide. | Verify DNS/TLS, sign-in, worker stages, source credentials, evidence and one monitored change. Record recovery time and missing-data window. |

Preserve logs and backups under restricted access. Keep customer-facing communication factual and avoid putting tokens, raw plans, or evidence payloads into incident channels. Assess contractual and regulatory notification requirements with the responsible owner. Review the root cause and track remediation and regression tests after recovery.
