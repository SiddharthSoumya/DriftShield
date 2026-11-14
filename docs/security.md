# Security and trust boundaries

DriftShield reads GitHub repository configuration. A short-lived installation token is scoped to repository Metadata read and to a repository ID for collection. The app does not request write permissions or store source code. The polling worker obtains observations directly from GitHub. Repository settings and findings are still customer data.

Workspace access requires a Django session and membership checked on each request. Owners manage policy, notifications and imports. Operators handle scans and baseline/incident actions. Viewers can see and export evidence. The server administrator provisions users, revokes access and binds installations, and is trusted across workspaces. The database login and GitHub encryption key are service-level credentials. Use a separate deployment when customers need an independent administrative boundary.

Mutations use CSRF tokens. Production uses HTTPS redirects, host validation, secure session/CSRF cookies, HSTS for the origin, restrictive content security policy, and login throttling. The web service listens on loopback. Use a trusted proxy that replaces the forwarded protocol header. Both services run without root privileges. The service config contains secrets and must be root-owned and writable only by root.

GitHub private keys use authenticated encryption in the database. Keep `DS_GITHUB_ENCRYPTION_KEY` backed up with restricted access. An installation key can be rotated through the CLI. Rotating the service encryption key requires decrypting and re-encrypting existing App keys in a maintenance operation or rebinding each installation. Protect PostgreSQL files, backups and SMTP configuration. Avoid copying raw settings into third-party systems without customer approval.

Alert emails contain repository names and finding categories. Keep recipients under the customer's control. The browser prevents cross-workspace access at the application layer. Independent testing and PostgreSQL row-level policies should precede a broadly hosted multi-tenant rollout. SSO, MFA, penetration testing and formal compliance attestations are outside v3.
