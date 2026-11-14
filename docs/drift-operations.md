# Infrastructure findings workflow

Open **Infrastructure → Findings queue**. Filter by actionable, excepted, resolved or all findings, by priority, or by assignments to you. The CSV export uses the same filters and streams up to 10,000 rows. Each target also has an evidence timeline and a separate timeline export.

## Assign, review and verify

1. Open a finding and compare its changed fields with the approved values. Assign an active workspace owner or operator. An unassigned finding remains visible to all workspace members.
2. Open the target's runbook if configured. Record a review or change-ticket reference against the current observation. Apply changes through the team's existing IaC, GitOps or operations process.
3. For inventory targets, a matching observation captured after the review is required to mark restoration as verified. An earlier matching observation leaves the reviewed finding open. A clean Terraform/OpenTofu refresh-only report is reconciliation evidence.
4. For an intentional change, accept the observed resource into the baseline where supported. End active exceptions affecting the resource before accepting it. A changed observation invalidates a stale review form.
5. Export the finding or target timeline. The timeline records opening, assignment, review, baseline approval, exception transitions and closure after the upgrade. Existing findings retain their previous data and may have no opening event or known opening baseline version.

Only owners can configure a runbook link. The browser opens it when clicked. The server and collector do not fetch the link or execute the instructions. Use an HTTPS link without credentials, a query string or a fragment.

## Expected changes

An owner can grant an exception for an exact expected-to-observed value pair on a single target, resource and field. An exception requires a reason and expires after 1–168 hours. The initial allowlist is replicas, desired capacity, memory bytes and nano CPUs, when both values are integers. An exception cannot suppress an added or missing resource, a missing field, a change to another value, a security field such as privileged mode, or a Terraform refresh finding.

An entirely covered finding stays visible as **Excepted**. It continues accumulating observations and remains part of the approved-versus-observed comparison. Any uncovered change stays actionable. Expiry or manual termination reevaluates the last observation and can make a finding actionable without incrementing the observation count. Email includes that observation's timestamp. If collection is stale or failed, run the collector again before acting. Up to 100 active exceptions are allowed per target.

Owners can enable high-priority-only infrastructure drift email in **Settings**. This leaves medium-priority findings in the queue and does not filter collection-failure, overdue or credential alerts. The existing notification recipient and outbox handle delivery.

## Staging acceptance

Upgrade an existing installation through the [operations guide](operations.md) and confirm prior findings still open. In a staging target, record a baseline, make a supported change, assign it, and check the field-level diff and export. Except an approved scaling value, then confirm a different value or another field still opens actionable drift. End the exception, restore the setting through the team runbook, and verify a newer observation closes the reviewed finding. Confirm actual email delivery and external worker alerting in the deployment.

The workflow is built around scheduled observations and customer-run remediation. It does not offer cloud event ingestion, billing attribution, automatic rollback, or an independent tamper-evident ledger. See [release gates](release-readiness.md) for customer-hosted rollout.

## EBS pilot workflow

Request an initial baseline from a fresh complete EBS observation. A distinct named owner approves it with the change ticket retained in the record. Later baseline proposals select specific open findings. Pilot exceptions are unavailable.

Observed restoration means a fresh complete scan showed the expected configuration. Verified remediation also links an approved review and execution record to a later complete scan. A missing volume waits for a distinct owner's independent AWS check and reference. The independent check leaves a finding open when the missing volume is expected by the baseline. Accept that change only through baseline approval.

Retiring a target revokes its runner and preserves history. The owner can download the protected evidence bundle from the retired target. Verify its manifest before storing it in the agreed customer evidence location.
