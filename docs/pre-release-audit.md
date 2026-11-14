# DriftShield pre-release audit

Baseline reviewed: [main at eec9fe0](https://github.com/SiddharthSoumya/DriftShield/commit/eec9fe026c433b60ccd222e5102cc43eec34cb3b). Scope includes Django routes, model constraints, workspace roles, collector intake, provider adapters, background work, deployment files, CI, and product workflows. Findings describe defensive failure scenarios. Severity is based on impact and prerequisites. See [incident response](incident-response.md) for operator actions.

## Decision and scores

| Dimension | Score after changes | Basis |
| --- | --- | --- |
| Security | 70/100 | Scoped permissions, TLS checks and credential lifecycle. Remaining risks include shared administrative trust, lack of MFA and unverified collector evidence. |
| Reliability | 67/100 | Transactional writes, duplicate handling, bounded provider operations, worker-stage health and automated recovery checks. Production dependency, load and regional recovery evidence remain open. |
| Production readiness | 55/100 | Repeatable installation and operator runbooks. Shared hosting, compliance evidence, live provider validation and capacity guarantees remain open. |

These are engineering judgments against a public SaaS launch, not measured assurance levels. **No-ship for open multi-tenant SaaS or a million-user claim. Conditional ship for a supported, dedicated customer deployment after its staging gates pass.**

## Security findings

No critical code vulnerability was confirmed in this review. A shared control-plane or privileged host compromise would have critical impact. It is an open architecture risk with a privileged-access prerequisite, not a verified application bypass.

| ID / severity | Scenario and technical impact | Business impact | Mitigation and implementation |
| --- | --- | --- | --- |
| S1 / High | A remote database could negotiate unverified or plaintext transport, and remote SMTP could be configured without TLS. An untrusted network path could expose credentials or records. | Customer evidence and credentials can be exposed. | **Implemented.** Require verified PostgreSQL TLS for remote production hosts and encrypted remote/authenticated SMTP. Add the DB CA path and use provider certificates in staging. |
| S2 / High | Collector bearer credentials had indefinite life. Compromise authorizes submissions within one target. A syntactically valid report can still misrepresent reality. | False clean status or false drift can cause costly decisions. | **Partially implemented.** Owner revocation, expiry, rotation and mandatory fresh observation after rotation. Legacy tokens receive 30 days from upgrade. New tokens receive 90 days. Require dedicated runners, short-lived platform identity and independent source verification for stronger assurance. |
| S3 / High | A user could begin a mutation before their membership was revoked and commit after the original role became stale. | A departed operator could affect baseline or monitor integrity. | **Implemented for interactive writes.** Lock the workspace, re-read and lock membership, and then mutate. In-flight reads already authorized may finish. |
| S4 / High | A valid runner could accidentally scan a different Docker daemon, container set, or Kubernetes context under the same user-supplied label. | Missing or unrelated resources can produce misleading findings. | **Partially implemented.** Pin observed source IDs after first binding for updated Docker/Kubernetes/AWS collectors. Reject changes and missing IDs after binding. This protects against accidental scope changes. Independent inventory and attestation remain open. |
| S5 / High | Password-only access and shared database/process credentials mean account or administrative compromise can expose workspace records, with cross-customer impact if one instance is shared. | Customer disclosure and trust loss. | **Open for hosted release.** Dedicated deployments for the pilot. Add MFA/SSO, service separation, DB-enforced tenant isolation and independent testing. Current browser authorization uses server-side sessions and tenant-scoped ORM queries. |
| S6 / High | Mutable Actions tags and unpinned transitive packages allow build inputs to change. Root runs the reviewed installer and installs packages. | Compromised release or customer host. | **Partially implemented.** Pin Actions by verified commit, disable checkout credential persistence, bound CI time and restrict installer package inputs to wheels. Keep dependency audits. Still require hash-locked dependencies, pinned CI images, SBOM, release provenance, signature verification, and protected release approval. |
| S7 / High | A request flood can consume sync workers or DB lookups before accepted-observation throttling applies. Login quotas and multiple customers share the same instance. | Outage and stale monitoring. | **Open at scale.** Put edge quotas, trusted client identity, connection limits and body limits in the deployment. Benchmark at expected and peak traffic. DB timeouts and streaming exports bound some resource use. Application limits are not DDoS protection. |
| S8 / Medium | Repeated notification tests can burden SMTP. A previously claimed email may race with a recipient change. | Mail reputation and confidentiality risk. | **Partially implemented.** Per-workspace test-mail cooldown, audited queueing, and current-recipient recheck before delivery. Already handed-off mail cannot be recalled. Use approved recipients, relay quotas and an incident procedure. |
| S9 / Medium | Access logs included query strings and potentially arbitrary client-supplied values. | Accidental disclosure through log aggregation. | **Implemented for Gunicorn.** Log method, path, status and latency without query strings or request headers. Configure the edge and log pipeline similarly, restrict access, and set retention. |
| S10 / Medium | Workspace audit and findings share the mutable database with application data. Target removal cascades into evidence deletion. | Evidence cannot independently prove integrity or satisfy a legal hold. | **Open.** Export evidence before removal. For regulated use, add external append-only storage, verified export manifests, role separation, and legal-hold policy. |
| S11 / Medium | Bootstrap secrets and GitHub private keys depend on deployment ownership. A host administrator can access the service configuration and decrypt stored integration credentials. | A server compromise may expose repository metadata across installations. | **Open architecture boundary.** Dedicated host, restricted config/backups, separate App per trust boundary, revoke upstream on compromise. Use a secrets manager and scoped worker access before shared SaaS. |
| S12 / Low | A user can open downloaded evidence in a spreadsheet or share it outside the workspace. | Offline disclosure or unsafe interpretation. | Existing CSV formula-prefix escaping and attachment headers reduce risk. Use a restricted evidence destination, approved sharing, and retention. Require an export policy when records are sensitive. |

### Security coverage notes

- **Authentication and sessions.** Django authenticates users with server-side sessions, secure production cookies and CSRF-protected mutations. Password reset is an operator CLI action. There is no public reset endpoint or browser OAuth/OIDC flow. GitHub App JWTs are short-lived outgoing credentials, separate from user sessions. MFA, centrally managed identity, and formal credential lifecycle evidence are outstanding.
- **Access control.** Scoped queries, owner/operator/viewer checks, mutation-time membership rechecks, and regression tests cover representative cross-tenant requests. No verified IDOR, auth bypass or cross-workspace data access was found in reviewed paths. This is not exhaustive verification of all hosting boundaries.
- **Injection and exposure.** The reviewed routes use the Django ORM and autoescaped templates. No raw user-controlled SQL, NoSQL, XML parser, upload endpoint, or shell invocation from the HTTP application was identified. Provider commands use argument lists on a trusted runner. GitHub calls use a fixed HTTPS host and restricted pagination. Deployment configuration is executable shell and must remain root-controlled. Keep provider credentials out of custom snapshots and validate proxy and filesystem permissions on the real host.
- **Secrets and dependencies.** GitHub App keys are encrypted in the database. Collector credentials are high-entropy tokens stored as hashes. Backups, TLS trust, secret rotation, package integrity and private vulnerability reporting require an operator process. An advisory scan checks known advisories for resolved Python packages. It does not measure OS, firmware, third-party service, or zero-day exposure.

## Functional and QA review

| Scenario | Impact | Result or recommended fix |
| --- | --- | --- |
| Revoke or downgrade a member while a write is pending. | A stale role could permit a final write. | Workspace/membership recheck added and tested. |
| Present an expired token or rotate it while a previous observation is displayed. | Collection or approval could incorrectly continue. | Expiry/revocation regression coverage. Rotation invalidates the prior observation for approval. |
| Switch a bound target to a different observed source. | False removals, restoration or clean state. | Source mismatch rejected and state preserved. New target required for deliberate rebinding. |
| Change an SMTP recipient while a notification is queued. | Mail can be delivered to a previous address. | Queue invalidation and recipient recheck. A message already being sent remains outside recall. |
| Lose an upstream response or observe an incomplete inventory. | False negative if a provider gives a valid-looking incomplete result. | Preserve last good evidence on reported errors. Cross-check coverage, identity and API completeness in staging. |
| Apply a Terraform state-only reconciliation. | A clean report does not prove live rollback. | Existing `reconciled` status and independent verification rule retained. |
| Export a large evidence set. | Synchronous workers can exhaust memory or spend time on slow clients. | CSV streams bounded chunks with an explicit row limit. Measure throughput. Queue and pre-generate large exports for hosted scale. |
| Worker fails after starting a loop. | Old heartbeat could look healthy despite failed work. | Worker stage ticks advance only on completed operations. The health route reports failed or stale stages. |
| A new Docker engine, Kubernetes API endpoint or provider change alters its identifier. | Intentional cutover pauses ingestion for the old target. | Documented new-target and baseline workflow. Preserve old evidence when migrating. |
| Legitimate autoscaling or short-lived drift occurs. | False positive or missed event. | Open. Add explicit exceptions and event-based coverage for required providers. |
| Deploy a change with incompatible migrations. | Upgrade can interrupt both web and worker. | Existing staged build and health checks. Restore matching backups before rolling back incompatible schema. Zero-downtime and rollback rehearsal remain open. |
| A collector cannot deliver after its finite retries. | The observation may be lost. | Overdue detection and external monitoring. Open: durable, encrypted runner spool with bounded replay policy. |

## Non-functional and production review

| Weakness | Risk and impact | Recommendation |
| --- | --- | --- |
| Sync web workers and shared workspace locks | Latency and lock contention during large concurrent imports or intake | DB lock/statement limits and streaming reduce exposure. Split web, collector ingestion, and worker roles and benchmark realistic payloads. |
| One supported systemd host and DB dependency | Host or database outage stops service | Dedicated pilot with monitored backups. Use HA database, multi-instance application and tested failover before availability commitments. |
| Shared worker stages and SMTP at-least-once delivery | Head-of-line blocking or duplicate mail after an interrupted acknowledgement | Stage health is visible. Add independent worker pools, queue age metrics, and provider delivery identifiers where supported. |
| Limited alert coverage | Worker cannot report its own complete failure | Use independent HTTP checks, worker-stage monitoring and an external pager. Agree owners and escalation times. |
| Evidence and retention costs | Storage and response cost grow with monitor count and churn | Measure actual workload, define quotas and verify storage growth. Baseline history, backups and open findings need separate retention rules. |
| Configuration flags and deployment safety | Global config changes have a broad effect | Use staged rollout with versioned configuration. Add scoped kill switches before hosted releases and verify controls in a dedicated test environment. |
| Accessibility and usability | Operators could miss a critical state or find evidence slow to navigate | Current semantic templates and text statuses help. Run keyboard, screen-reader, contrast and mobile checks. Add evidence filters and pagination before high-volume use. |
| Incident preparedness | Recovery depends on an operator and available backups | Runbooks provided. Exercise on-call paging, key rotation, restoration and communications with named owners. |

## Compliance readiness

| Framework | Status and gap |
| --- | --- |
| GDPR | Technical support includes scoped access and configurable retention. Confirm controller/processor roles, data inventory, lawful basis and notices, Article 28 terms, technical and organizational measures, rights workflows, backup deletion and incident notification obligations before onboarding. [Regulation](https://eur-lex.europa.eu/eli/reg/2016/679/oj/) |
| Other privacy laws | Determine applicable regimes by customers, hosting and data subjects. For India, map the DPDP Act and phased rules to the service. Assess any additional jurisdictions with counsel. [MeitY](https://www.meity.gov.in/documents/act-and-policies/digital-personal-data-protection-rules-2025) |
| SOC 2 | No examination evidence. Define scope, owners, controls and evidence collection for the applicable trust services criteria. Produce operating evidence and obtain an independent examination if needed. [AICPA](https://www.aicpa-cima.com/resources/download/get-description-criteria-for-your-organizations-soc-2-r-report) |
| ISO/IEC 27001 | No certification evidence. Establish the ISMS, risk treatment, Statement of Applicability, internal audit and management review. [ISO](https://www.iso.org/standard/27001) |
| OWASP ASVS 5.0 | Partial technical coverage. Complete a scoped Level 2 control matrix, independent verification and remediation evidence before asserting conformance. [ASVS](https://asvs.owasp.org/) |
| OWASP Top 10 2025 | Reviewed major applicable classes and trust boundaries. Gaps remain in auth, design, supply chain, monitoring and exceptional conditions. Use ASVS for verification. [OWASP Top 10](https://owasp.org/projects/top-ten/) |

## Fault and chaos matrix

| Event | Expected behavior | Verification or next step |
| --- | --- | --- |
| Database outage | Liveness remains up. Readiness returns 503. DB-dependent requests fail and the worker retries. | CI stops and starts its own test PostgreSQL container and checks recovery. Load balancer must use readiness. |
| Cache outage | No external cache in the supported deployment. | Reassess if cache becomes part of auth or throttling. |
| GitHub/AWS/Kubernetes failure | GitHub uses backoff. Collector reports failure and preserves last good observation when intake is reachable. | Fixtures cover representative failures. Stage real provider permissions and outage behavior. |
| Queue unavailable | Outbox uses PostgreSQL. No independent queue failover. | Exercise a backlog and worker restart. Verify oldest pending age and replay behavior. |
| Storage unavailable or full | Database writes or backups can fail. Evidence cannot advance. | Rehearse storage exhaustion and restore in staging. CI exercises dump and restore to a separate database. |
| Network latency spike | Timeouts and bounded retries may lead to missed scans or delayed mail. | Inject latency on staging dependencies and verify error budgets and alert routing. |
| Traffic increases 100× | Worker, lock and DB connection saturation are plausible. | No 100× benchmark was run. Establish a defined workload, p95 latency, queue age, error rate and DB budget before promising scale. |
| Region outage | Single-region deployment stops until service and data are restored elsewhere. | Establish recovery targets and test cross-region recovery. No automatic regional failover is implemented. |

## Abuse-oriented review and trust

Defensive review considered stolen sessions or collector credentials, stale roles, cross-tenant object access, bulk export pressure, invalid and duplicated observations, misleading baselines, dependency compromise, and network or worker exhaustion. Main residual concerns are compromise of an authorized source, blind spots in sampled coverage, load on shared infrastructure, and database-administrator access to evidence. Critical changes need an independent provider check. Customer communications should state supported resource types, monitoring intervals, false-positive expectations, and who applies a fix.

## Release blockers and priorities

1. **Dedicated paid pilot:** merge reviewed changes, run live AWS and SMTP in the customer's staging environment, validate the actual proxy and secrets, test external paging, and demonstrate a backup restore with measured recovery. Keep independent checks for high-impact findings.
2. **Public hosted release:** complete MFA/SSO, independent tenant isolation testing, database-level boundaries, stronger machine identity, edge controls, HA and load/chaos evidence. Establish vendor, incident and privacy controls. Obtain independent assurance where customers require it.
3. **Nice to have after the gates:** filtered and scheduled evidence export, scoped policy exceptions, billing correlation, additional provider adapters, and guided integrations for customer-owned remediation workflows.

No production attack simulation, external penetration test, compliance certification, live AWS credential use, million-user load test, or regional failover drill is claimed here.
