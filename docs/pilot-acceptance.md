# Dedicated EBS pilot acceptance

## Prepare the host

Provision PostgreSQL 16+, a private TLS origin, a customer MFA access edge, a read-only AWS runner and separate probe and pager. The runner identity needs `ec2:DescribeVolumes`. Confirm its STS account and role before enrollment. Use separate named identities for the primary runner operator and the owner corroborating an absence.

Run the reviewed installer once to create `/etc/driftshield/service.env`. Set the dedicated workspace slug, HTTPS origin, PostgreSQL and SMTP settings. Set `DS_PILOT_EBS=1` and `DS_PILOT_RECOVERY_FENCED=1`. Generate a separate probe credential with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` and share it through the customer secret store. Rerun the installer from the reviewed release checkout. Keep the current release SHA and backup point with the customer change record.

Configure `deploy/Caddyfile.pilot.example` with a hostname and three disjoint allowed CIDR sets. The browser set must only contain the customer identity-aware edge, which enforces MFA on every user. The origin does not authenticate an identity from forwarded headers. The edge, runner and monitor should be separate network clients. Keep port 8000 on loopback and PostgreSQL private. Verify `caddy validate --config /etc/caddy/Caddyfile` and `systemd-analyze verify /etc/systemd/system/driftshield-web.service /etc/systemd/system/driftshield-worker.service` before opening any route.

Run the following from the host using values from the approved change record. The enrollment command prints a token once on the local terminal. Store it in the runner's secret store.

```bash
sudo driftshieldctl provision "$DS_PILOT_WORKSPACE" "$DS_PILOT_OWNER" --role owner
sudo driftshieldctl provision "$DS_PILOT_WORKSPACE" "$DS_PILOT_SECOND_OWNER" --role owner
sudo driftshieldctl pilot_enroll --workspace "$DS_PILOT_WORKSPACE" --scope "$DS_PILOT_EBS_SCOPE" \
  --role-prefix "$DS_PILOT_ROLE_PREFIX" --owner "$DS_PILOT_OWNER" \
  --runner-operator "$DS_PILOT_OWNER" --runner-label "$DS_PILOT_RUNNER_LABEL" --ticket "$CHANGE_TICKET"

```

For the isolated staging instance only, set `DS_PILOT_STAGING=1` and open the allowlisted runner and browser routes. Set the fence to 0 in `service.env`, restart web and enable and start the worker with `sudo systemctl enable --now driftshield-worker.service`. Start the [runner schedule](infrastructure.md#enrolled-ebs-pilot-runner). Do not accept production reliance before the following gates pass.

## Run the acceptance gates

| Gate | Execute | Pass condition |
|---|---|---|
| Network and MFA | Try app and database from an external network. Try browser from runner and probe networks. From the approved browser edge require MFA for both named owners. | No external route. Only the runner endpoint is reachable by the runner and only health by the probe. Both humans authenticate using MFA and individual app accounts. |
| Offboarding | Provision a disposable named member, open an app and edge session, revoke the IdP entitlement and run `sudo driftshieldctl revoke "$DS_PILOT_WORKSPACE" "$DS_PILOT_OFFBOARD_USER"`. Retry both sessions. | The removed user cannot view or mutate the workspace. Record IdP session revocation or its enforced TTL. |
| Collection | Collect the full staging region with the enrolled role. Use a disposable volume for a customer-operated change to an EBS field in scope. Repeat with a denied read and a known partial inventory fixture. | Complete inventory and real drift alert arrive. Denied and partial collection leave the finding open and show lost coverage. Wrong scope and expired/revoked credentials are rejected. |
| Baseline and restoration | Request and approve a baseline with two users. Attempt to approve with the requester. Record a remediation review and execution reference, approve with the second owner and collect again. Also restore a change without an approved review. | Self-approval fails. Observed restoration and verified remediation have distinct outcomes and evidence. |
| Disappearance | Remove a disposable unexpected volume through the authorized customer workflow. Inspect the resulting candidate. Have a distinct owner attest after an independent AWS check. | The first omitted report does not close the case. Valid corroboration closes an unexpected-volume case. A confirmed missing expected volume remains open pending explicit baseline approval. |
| Evidence | Retire a disposable target in staging, then download its ZIP as owner. Set `DS_PILOT_EVIDENCE_BUNDLE` and run the acceptance command below. | Export remains available after retirement. Every checksum and row count verifies. Store the bundle and custody record outside the host. |
| Worker and runner | Stop the staging worker and then restart it. Separately stop the runner timer past one cadence plus grace, then run a fresh scan. | Independent pager identifies each incident and recovery. UI reports stale and records the coverage gap. |
| Mailbox | Cause a real SMTP rejection and separately route a canary so the relay accepts it without delivery to the monitored mailbox. Restore normal routing. | The independent incident path receives both pages. A real drift message and later canary are independently observed in the actual mailbox. |
| Recovery | Follow the isolated recovery drill below. | Agreed RTO and RPO pass. Revoked access and old runner tokens stay revoked. The first fresh complete observation and case on the recovered host work. |

From the probe network run:

```bash
DS_PILOT_EXPECTED_STATE=healthy ./deploy/pilot-acceptance
```

Run the same command with `unhealthy`, `recovery_fenced` or `database_unavailable` when exercising those states. Configure the customer monitor to poll health every minute, page after two consecutive failures, distinguish worker age ≥300 seconds and notification age ≥600 seconds, and track collector stale, failed, partial or inactive outcomes. Before enrollment suppress only the collector condition with an expiry. Page through a separate channel. Verify the independent monitor itself has an external missing-heartbeat alert.

Configure the customer mailbox monitor for a message with exact subject `DriftShield mailbox probe YYYYMMDDTHHMMZ` in every UTC 15-minute slot. Page if the matching message has not arrived ten minutes after the slot. Use one incident per condition with recovery and deduplicate repeated Message-IDs. Relay acceptance alone does not satisfy the check. Keep recipient routing, probe and pager identifiers with the acceptance evidence.

## Recovery and rollback

Agree the recovery objectives and drill on a private isolated host and database with no production SMTP route. Securely capture database backup, protected configuration and keys, release SHA, evidence bundle checksum, and most recent delivery and approval boundary in separate storage. Record the start time and recovery point.

1. Close intake and browser writes at the private edge. Stop `driftshield-worker.service` before any restore. Fence both hosts against the same outbox and runner. Record the current release path with `readlink -f /opt/driftshield/current`.
2. On the isolated replacement host restore to an empty database, using protected PostgreSQL credentials and customer database tooling. Use `pg_restore --list "$PILOT_DB_BACKUP"` and `pg_restore --exit-on-error --dbname "$PILOT_RESTORE_DATABASE" "$PILOT_DB_BACKUP"`. Restore protected configuration and keys separately. Install the matching release with `DS_PILOT_RECOVERY_FENCED=1`. Verify the migrations and local DB readiness.
3. Compare restored records with the external backup manifest, current IdP membership and change approvals. Revoke any restored access that should be inactive. Inspect `pilot_recovery reconcile` with `--dry-run`, then run the mutation:

```bash
sudo driftshieldctl pilot_recovery reconcile --workspace "$DS_PILOT_WORKSPACE" \
  --target "$DS_PILOT_TARGET" --ticket "$RECOVERY_TICKET" --operator "$RECOVERY_OPERATOR"
sudo driftshieldctl pilot_recovery issue-runner --workspace "$DS_PILOT_WORKSPACE" \
  --target "$DS_PILOT_TARGET" --ticket "$RECOVERY_TICKET" --operator "$RECOVERY_OPERATOR"
```

4. Store the new runner credentials in the private secret store. On the isolated rehearsal, clear the fence while production routes remain closed. On cutover, stop the old web and worker and keep browser writes and production mail closed. Record production activation for the new recovery ticket as described below. Open only the runner route, clear the fence, and restart web. Verify a fresh complete observation before enabling browser mutations or enabling and starting the worker with the agreed relay. Check external health, membership, token invalidation, evidence checksum and inbox receipt.
5. Append the recovery record and save the result with the external backup evidence:

```bash
sudo driftshieldctl pilot_recovery record --workspace "$DS_PILOT_WORKSPACE" \
  --target "$DS_PILOT_TARGET" --ticket "$RECOVERY_TICKET" --operator "$RECOVERY_OPERATOR" \
  --started "$RECOVERY_STARTED" --ended "$RECOVERY_ENDED" --recovery-point "$RECOVERY_POINT" \
  --backup-ref "$RECOVERY_BACKUP_REF" --checksum "$EVIDENCE_SHA256" --release "$RELEASE_SHA" \
  --rto-target-seconds "$RTO_SECONDS" --rpo-target-seconds "$RPO_SECONDS" \
  --loss-seconds "$MEASURED_LOSS_SECONDS" --checks "$RECOVERY_CHECKS_JSON"
```

The checks JSON contains booleans for `access`, `credentials`, `evidence`, `collector`, `mailbox`, and `rollback`. A failed drill is recorded and exits nonzero.

Only repoint to the previous release against the same database when compatibility has already been exercised and no incompatible writes occurred. Stop both units, preserve the current release and DB, then use `ln -sfn "$PREVIOUS_RELEASE" /opt/driftshield/current.new` and `mv -Tf /opt/driftshield/current.new /opt/driftshield/current` as root. Restore matching unit files if they changed, reload systemd, start web and keep the worker stopped until checks pass. After pilot writes, do not run the legacy release against the new database or reverse the evidence migrations. Restore the compatible release and backup to a separate host and reconcile before opening the private routes.

## Sign-off

Keep the release SHA, proof from each acceptance gate, measured recovery results, evidence custody and retention, support hours, primary and backup owners, and buyer sign-off in the customer change record. Keep retired target data until the agreed handoff and retention window. Teardown that would delete database evidence requires separate customer authorization and a verified protected export.

### Record production activation

Keep `DS_PILOT_STAGING=0` and `DS_PILOT_RECOVERY_FENCED=1`. Set `DS_PILOT_ACCEPTANCE_TICKET` in the service environment to the approved change ticket. After completing the staging acceptance gates, record the passing recovery drill for the installed release and use the customer-approved acceptance record:

```bash
sudo driftshieldctl pilot_check --workspace "$DS_PILOT_WORKSPACE" --activate \
  --ticket "$DS_PILOT_ACCEPTANCE_TICKET" --operator "$RECOVERY_OPERATOR" \
  --recovery-ticket "$RECOVERY_TICKET" --acceptance-ref "$PILOT_ACCEPTANCE_REF"
```

Clear the fence only after the command succeeds. Keep browser writes and production mail closed until the new runner has submitted a fresh complete observation. A later reconciliation invalidates the earlier activation and requires a passing recovery record and a new activation.
