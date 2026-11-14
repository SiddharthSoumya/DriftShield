# Security and trust boundaries

DriftShield reads GitHub repository configuration. A short-lived installation token is scoped to repository Metadata read and to a repository ID for collection. The app does not request write permissions or store source code. The polling worker obtains observations directly from GitHub. Repository settings and findings are still customer data.

Workspace access requires a Django session and membership checked on each request. Owners manage policy, notifications and imports. Operators handle scans and baseline/incident actions. Viewers can see and export evidence. The server administrator provisions users, revokes access and binds installations, and is trusted across workspaces. The database login and GitHub encryption key are service-level credentials. Use a separate deployment when customers need an independent administrative boundary.

Mutations use CSRF tokens. Production uses HTTPS redirects, host validation, secure session/CSRF cookies, HSTS for the origin, restrictive content security policy, and login throttling. The web service listens on loopback. Use a trusted proxy that replaces the forwarded protocol header. Both services run without root privileges. The service config contains secrets and must be root-owned and writable only by root.

GitHub private keys use authenticated encryption in the database. Keep `DS_GITHUB_ENCRYPTION_KEY` backed up with restricted access. An installation key can be rotated through the CLI. Rotating the service encryption key requires decrypting and re-encrypting existing App keys in a maintenance operation or rebinding each installation. Protect PostgreSQL files, backups and SMTP configuration. Avoid copying raw settings into third-party systems without customer approval.

Alert emails contain repository names and finding categories. Keep recipients under the customer's control. The server scopes application queries to the signed-in user’s workspace membership. Independent testing and PostgreSQL row-level policies should precede a broadly hosted multi-tenant rollout. SSO, MFA, penetration testing and formal compliance attestations are outside v3.

## Infrastructure evidence

Native ingestion validates resource field names and types, rate-limits accepted observations per target, and checks that retries of a delivery agree with its saved content hash. The hash identifies a retry. A target bearer token authenticates the collector, and the collector supplies the evidence. Owners can rotate or revoke the credential. New and rotated credentials expire after 90 days. Existing credentials have a 30-day window from the upgrade migration. A compromised collector or leaked token can submit false but well-formed observations. Treat findings as operational evidence, independently confirm critical changes and rotate the credential when runner integrity is uncertain. Short-lived machine identity and cryptographic provenance are future work.

Use one trusted runner and stable scope per target. Updated AWS, Docker and Kubernetes collectors send an observed source ID. The first identified observation binds it, and later mismatches are rejected. A schema cannot establish that an upstream inventory is complete. Monitor the health of the collector, account and permissions independently. The custom snapshot protocol accepts operator-defined scalar fields and cannot determine which strings are secrets. Never send credentials or raw configuration through that interface.

Apply reverse-proxy body limits and timeouts before enabling public ingestion. Keep the backend loopback-only, use an independently authenticated monitoring path for service and worker health, and add edge throttling suited to your network for high-volume deployments.

The EBS pilot uses a dedicated host and database, a private MFA-enforcing customer access edge, and separate network paths for collector and probe. The enrolled bearer and AWS scope checks constrain submission. Independent resource-absence confirmation is a recorded customer attestation with an external evidence reference. [Pilot deployment gates](pilot-acceptance.md)
