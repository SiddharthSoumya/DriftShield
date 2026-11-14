# Web and HTTP interfaces

The browser uses HTML forms with Django sessions and CSRF tokens. Successful mutations redirect to their destination. Errors are presented on the resulting page. Workspace membership is checked server-side. Guessed workspace or monitor IDs outside the caller's access return 404. A known workspace with an insufficient role returns 403 for its restricted actions.

| Method and path | Role | Input or response |
| --- | --- | --- |
| GET, POST `/login/` | Public | Username/password with CSRF, throttled attempts |
| POST `/logout/` | Signed in | End the session |
| GET `/` | Signed in | Workspace selection or redirect |
| GET `/w/{slug}/` | Member | Overview and repository list |
| POST `/w/{slug}/repos/add/` | Owner, operator | `repository` in owner/name form |
| POST `/w/{slug}/repos/import/` | Owner | Import selected installation repositories up to the workspace limit |
| GET `/w/{slug}/repos/{id}/` | Member | Observation, baseline, findings and history |
| POST `/w/{slug}/repos/{id}/action/` | Owner, operator | `action` of scan, pause, resume, ack, or approve |
| POST `/w/{slug}/repos/{id}/remove/` | Owner | Exact repository name in `confirm` |
| GET `/w/{slug}/audit/` | Member | Latest 100 workspace actions |
| GET, POST `/w/{slug}/settings/` | Owner | Recipient, retention, policy, weekly digest, optional test email |
| GET `/w/{slug}/evidence.csv` | Member | Most recent 10,000 incident records |
| GET `/livez` | Public | Web-process liveness without a database query |
| GET `/healthz` | Public | JSON database readiness with 200 or 503 |
| GET `/w/{slug}/worker.json` | Owner | JSON worker and stage health with 200 or 503 |

Approval takes the last rendered `revision`, `version`, repeated `field` parameters and a non-empty `reason` of at most 240 characters. Acknowledgement takes `incident` and checks that the record belongs to the selected repository. CSV uses standard quoting and prefixes spreadsheet formula-like cells.

The browser routes use same-origin sessions. The versioned collector ingestion route below uses a target credential.

## Infrastructure interface

`POST /api/v1/infrastructure/{id}/observations/` accepts a scoped bearer credential and JSON with `schema: 1`, `platform`, `scope`, UUID `delivery`, timezone-aware `captured_at`, and either `resources` or `error`. Resource IDs map to allowlisted scalar fields. An identical retry returns the original status within the acceptance window. A conflicting reuse of a delivery ID returns 409. New accepted deliveries are limited to one per target per minute (429 with Retry-After). Stale order returns 409, wrong scope or invalid input 422, invalid token 401, and missing or oversized body 413. Results are `unbaselined`, `match`, `drift`, or `error`. Workspace owners create and remove targets and rotate or revoke collector credentials. Operators and owners can approve baselines and record remediation reviews. Viewers can inspect targets and export evidence.

A successful infrastructure envelope may include `source_id`. A bound target rejects absent or changed source IDs on later successful observations. Collector credentials expire and owners may revoke them without deleting the target. Both CSV endpoints stream up to 10,000 rows.

The infrastructure browser routes include `GET /w/{slug}/infra/findings/`, filtered `GET /w/{slug}/infra/evidence.csv`, and `GET /w/{slug}/infra/{id}/timeline.csv`. Filters are `state`, `priority` and `mine`. Owner-only mutations manage runbook links and exceptions. Owners and operators can assign and review findings.

## AWS EBS pilot observations

`POST /api/v2/infrastructure/<target-id>/observations/` accepts JSON up to 1 MB over HTTPS with the active target bearer. The collector sends its canonical `ACCOUNT:REGION:ebs` scope, `aws:ACCOUNT:REGION:ebs` source, enrolled UUID and epoch, delivery UUID and capture start/end with time zone. The capture window is at most five minutes, and end time must be no more than 15 minutes old or 30 seconds ahead. A complete report includes `projected_count` and `resources` with at most 500 EBS volumes. Partial and failed reports contain an allowlisted error and omit both fields. The JSON schema defines the exact shape. [Observation schema](schemas/ebs-observation-v2.json)

Accepted responses return `status`, `coverage` and `delivery`. [Accepted response schema](schemas/ebs-observation-response-v2.json) Status is `unbaselined`, `match`, `drift`, `awaiting_confirmation`, `partial` or `failed`. Exact replay returns the original response with current credentials and a valid time window. An altered replay, out-of-order report, report collected before enrollment, or report ending before an open coverage gap returns 409. Invalid enrollment, scope or body returns 422, invalid credentials return 401, disabled or non-pilot v2 returns 404, unsupported media returns 415, and oversized body returns 413. Throttling returns 429 with integer `Retry-After`. Recovery fencing returns 503. A valid pilot credential presented to v1 receives `pilot_v2_required` with 409.

The target action route accepts CSRF-protected enrollment, baseline and review requests and decisions, independent absence confirmation, retirement, and credential rotation or revocation. Approval and corroboration are owner actions. Owner-only `GET /w/<slug>/infra/<target-id>/evidence-bundle/` serves a point-in-time ZIP. Optional `from` and `to` are inclusive and exclusive row-creation times with offsets. Invalid ranges return 422. An export exceeding the build budget returns 409 with `export_range_required`.

`GET /internal/pilot/healthz` is private and uses a separate probe bearer. It returns 200 when healthy and 503 when unavailable, fenced or unhealthy. Worker age, collector coverage, pending notification age, terminal failures and relay state are reported without resource IDs. A relay acknowledgement only records handoff. [Health response schema](schemas/pilot-health.json)
