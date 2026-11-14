# DriftShield repository audit and product design

Audit target: [main at dfdf88f](https://github.com/SiddharthSoumya/DriftShield/commit/dfdf88f8fe16318f110839bb3f98db75d0e2cbbe).

## What you built originally

| Layer | Implementation | Consequence |
| --- | --- | --- |
| Detection | Java 21, Spring Boot 4.0.6 and SnakeYAML compare a bundled baseline YAML file against a local runtime YAML file every five seconds. | The app demonstrates configuration drift. It observes local files. No cloud collector, activity stream, model training, or inference is implemented in that path. |
| Comparison | The comparator iterates baseline keys and compares trimmed string values. Severity comes from a short key-based switch. | Runtime-only keys can be missed. Values with different types can compare equal. Severity reflects the key rather than the direction or blast radius of the change. |
| Restore | The engine saves each difference, rewrites the entire runtime YAML file, then marks events reverted. | A failed or interrupted restore can leave the file and event history out of sync. The rewrite lacks approval, a post-restore read, and a shared transaction. |
| API and data | Spring MVC, JPA/Hibernate, H2 by default, PostgreSQL dependencies, and Hibernate schema update. Read endpoints include full event history and runtime configuration. | The prototype has no application authentication or tenant boundary. Detection and demo restore are exposed as GET operations with side effects. |
| Dashboard | React 19, Vite 8, JavaScript and Axios poll event history and runtime configuration every five seconds. | The page prints a hardcoded secured status and logs fetch failures in the console. Health cannot be inferred reliably from the page. |
| Packaging | Maven, Docker, Compose, Nginx, a Render blueprint, and a GitHub Actions deployment workflow. | Compose starts Postgres without wiring the app to it, so the H2 default can still be used. The file readers and writer mix relative and container-specific absolute paths. |
| Tests and license | One Spring context-load test. The checked-in LICENSE is MIT. | The core diff and restore paths lack dedicated regression coverage. The MIT license supports distribution and self-hosting. |

Code references: [detection and comparator](../backend/backend/src/main/java/com/driftshield/backend/detection/drift), [remediation](../backend/backend/src/main/java/com/driftshield/backend/remediation/drift), [API controllers](../backend/backend/src/main/java/com/driftshield/backend/controller), [Compose](../backend/backend/docker-compose.yml), [dashboard](../frontend/src/App.jsx), [license](../LICENSE).

## Product and first buyer

Start with small engineering teams and development agencies that manage many GitHub repositories without a dedicated platform security team. The recurring job is to know when repository settings leave an approved state, find the exact field that changed, and track the outcome after someone investigates.

Example: A repository's default branch changes from `main` to `release`. DriftShield shows both values, when the change was first and last observed, and which approved version the scanner compared against. The team can acknowledge the incident, restore the setting in GitHub, or approve the intended change. A subsequent observation verifies the outcome. Polling establishes when the change was observed. Attribution requires an audit event from GitHub and may depend on plan and permissions.

Basic metadata makes onboarding easy to test. Coverage of effective default-branch rules, including review requirements and status checks, is the next product milestone and the first feature I would test for willingness to pay. GitHub exposes ruleset endpoints and plan-dependent coverage. An empty response can mean no rules apply or insufficient product coverage, depending on the endpoint and account. Treat permissions, unsupported fields, and failed collection as explicit states. See [GitHub rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets) and [rules API](https://docs.github.com/en/rest/repos/rules).

## Stack decision

| Component | Self-hosted pilot | Hosted release |
| --- | --- | --- |
| Application | Python 3.12, FastAPI, Uvicorn, and a small same-origin dashboard in HTML, CSS, and JavaScript. One process serves the API, UI, and public collector. | Keep the API and domain model. Add React and TypeScript with Vite when the dashboard requires richer filtering, organization navigation, and incident workflows. |
| Storage | SQLite in WAL mode on a persistent volume. | PostgreSQL, versioned migrations, tenant-aware constraints and queries, backups, and tested recovery. Add row-level policies as a second boundary. |
| Collection | Direct public API or a GitHub Actions runner in the customer's repository. | Read-only GitHub App with repository selection, short-lived installation tokens, rate-limit-aware jobs, webhook validation and periodic reconciliation. Preserve the runner interface for custom or isolated collectors. |
| Delivery | Operator dashboard with scan status, approved baselines, acknowledgement, and audit trail. | A transactional outbox for Slack and email, deduplication, delivery retry, per-organization routing and a test notification button. |
| Operations | Python locally or an OCI container on an existing host. | One application and one worker with PostgreSQL-backed jobs initially. Keep collection status and alert-delivery status observable. |

Spring Boot, PostgreSQL and React are still sound tools. A Java-oriented team could implement the same design using Spring Security, Flyway, JPA, and the existing React dashboard. For this rewrite the small Python service lowers setup cost and keeps the pilot runnable with one runtime. PostgreSQL makes sense once multiple users and workers share the hosted data. A single TypeScript stack is also viable for a TypeScript-oriented team, with the same requirement to run scheduled collection independently of browser requests.

Local development has no software license cost. Existing hosting can run a small pilot with a persistent disk. Render's free web and Postgres offerings are for evaluation, and free Render Postgres expires after 30 days. A managed PostgreSQL free tier such as Neon can support development after the PostgreSQL port. Budget for always-on ingestion, durable storage, backups, and email for a paid release. [Render free services](https://render.com/docs/free) · [Neon free plan](https://neon.com/blog/neon-free-plan-1-gb-per-project) · [GitHub Actions billing](https://docs.github.com/en/actions/concepts/billing-and-usage).

## Hosted release architecture

The web application manages workspaces, members, installations, monitors and incident triage. A job worker reads GitHub settings using installation credentials and writes observations through the same comparison service used by runner deliveries. A successful transaction stores the scan, updates the incident and adds a notification record when an incident opens or changes. A delivery worker sends notifications and records the result.

Key identities are the tenant, installation, GitHub repository ID, baseline version, observation ID, and field or rule path. Every hosted read and mutation must authorize the workspace server-side. Verify GitHub App install ownership and webhook signatures, persist delivery IDs for deduplication, and use immutable repository IDs across renames. Store an allowlisted projection of settings, minimize retention, and provide export and deletion. Report unavailable and overdue collection separately from a confirmed clean scan. Track initial onboarding and collection freshness before adding more integrations.

## Release sequence

1. **Self-hosted pilot.** Run it against a public repository you control and then a private sandbox using the workflow. Test changed, repeated, restored, unavailable and overdue observations. Use real collection tests to validate the documented permissions and fields.
2. **Paid beta.** Add effective GitHub rules, org-scoped GitHub App onboarding, workspaces with owner and viewer roles, Slack/email alerts, CSV export, and managed PostgreSQL. Verify tenant isolation, installation removal, API rate limits, backup recovery and alert redelivery before taking payment. Start with manual billing.
3. **After recurring use.** Add expiring maintenance exceptions with a reason, team ownership, audit-event attribution where available, organization templates, and scheduled evidence exports. Consider guided change requests. Expand to Terraform/OpenTofu or Kubernetes only when existing customers ask for the same workflow there. Keep any eventual remediation behind explicit customer approval and verify the final state with a fresh observation.

## Positioning and pricing hypothesis

GitHub rulesets enforce policy. Probot Settings syncs repository settings from YAML. Rewind backs up GitHub and supports some restores. DriftShield's proposed wedge is read-only, cross-repository drift triage with evidence history. HCP Terraform and Spacelift already offer infrastructure drift detection, so a broad Terraform product would need a separate reason to win. [Probot Settings](https://github.com/apps/settings) · [Rewind coverage](https://help.rewind.com/hc/en-us/articles/27238177595419-Backup-coverage-for-GitHub) · [HCP Terraform](https://developer.hashicorp.com/terraform/cloud-docs/workspaces/health) · [Spacelift](https://docs.spacelift.io/concepts/stack/drift-detection).

Recruit five teams or agencies that manage at least ten repositories each. Ask them to connect two repositories and walk through one intentional settings change. Track time to first approved baseline, percentage of monitors with a recent successful scan, actionable alerts, and teams still active in week four. Test a free hosted tier for one public repository and a paid workspace at $19–29/month for roughly 25 repositories with private monitoring, alert routing, and history. That price is a test. Collect willingness to pay before building a billing system or promising an SLA.
