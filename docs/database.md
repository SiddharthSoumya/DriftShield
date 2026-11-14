# Database design

Production uses PostgreSQL. Development can use SQLite. Migrations are in `governance/migrations` and apply through `manage.py migrate`.

| Table | Design |
| --- | --- |
| Workspace | Unique slug, display name, repository allowance, policy flags, alert email, weekly digest and retention |
| Membership | Unique workspace and user, with owner, operator, or viewer role |
| Integration | One per workspace, globally unique GitHub installation ID, app identifier, encrypted private key and account name |
| Monitor | Workspace, normalized name, immutable GitHub ID once discovered, collector, last observation, revision, due time, lease, baseline version |
| Baseline | Immutable version per monitor, approved values, actor, reason and creation time |
| Scan | Monitor, collection completion time, status and sanitized failure |
| Incident | Monitor, setting or policy, expected and observed values, severity, baseline version, first/last seen, acknowledgements and resolution |
| Audit | Workspace, optional monitor, action, actor, details and event time |
| Notification | Workspace, resolved recipient, subject/body, due time, lease, attempts and terminal state |
| WorkerHealth | Last heartbeat for the worker role |
| LoginAttempt | Hashed rate-limit key, attempt count and window |
| Django auth and sessions | Password hashes, account records and server-side sessions |

Workspace and repository identifiers form unique constraints. A partial unique index permits one open incident per monitor and field. A version is unique within a monitor. Rows are scoped through workspace membership in every interactive route. PostgreSQL row-level security is a possible additional boundary for a managed hosted deployment.

Hourly maintenance removes scans, resolved incidents, audit records, and notification history older than the workspace retention window. It also prunes expired sessions and login throttle records. Current state, open incidents and baseline versions are retained until monitor removal. Removing a monitor deletes its baselines, scans and incidents. Workspace audit retains the removal event. Export before removal if your policy requires historical evidence.

v1 and v2 use incompatible data models. v3 installs into a fresh PostgreSQL database. Export any earlier evidence independently and approve fresh baselines from live scans.

## Infrastructure tables

`Target` belongs to a workspace and stores platform/scope, hashed target token, latest observation, revision and baseline version. `Delivery` is unique per target and UUID. `Baseline` is versioned per target. `Finding` records expected/observed projections and has at most one open row per target/resource. Intake and review hold workspace and target row locks and update evidence and outbox atomically. Hourly maintenance prunes old deliveries and closed findings.
