"""Two-person approval and evidence-backed decisions for the EBS pilot."""
from datetime import timedelta

from governance.domain import audit
from . import pilot_intake
from .domain import Conflict, alert, fingerprint, risk, same
from .models import Baseline, Finding, PilotApproval
from .triage import record


def text_field(value, limit, label, *, required=True):
    value = value.strip()
    if len(value) > limit or (required and not value) or (value and not value.isprintable()):
        raise Conflict(f"Enter a valid {label}.")
    return value


def active_findings(target):
    return Finding.objects.filter(target=target, closed_at__isnull=True)


def basis(target, now):
    if pilot_intake.coverage_state(target, now) != "complete":
        raise Conflict("A fresh complete observation is required.")
    return target.last_complete_delivery


def decision_state(item, state, now, reason="", approver=None):
    item.state = state
    item.decided_at = now
    item.decision_reason = reason
    item.approver = approver
    item.save(update_fields=["state", "decided_at", "decision_reason", "approver"])
    record(item.target, f"approval_{state}", approver or "system", item.finding, approval=item.pk,
           kind_of_approval=item.kind, ticket=item.ticket, reason=reason)


def stale_reviews(target, now, reason, finding=None):
    queryset = PilotApproval.objects.filter(target=target, state="pending")
    if finding is not None:
        queryset = queryset.filter(finding=finding)
    for item in queryset.order_by("pk"):
        decision_state(item, "stale", now, reason)


def finding_proposal(finding):
    return {"id": finding.pk, "resource": finding.resource, "expected": finding.expected, "actual": finding.actual}


def confirmation(target, finding, delivery):
    event = finding.absence_confirmation
    return bool(event and finding.absence_delivery_id == delivery.pk
                and event.detail.get("baseline_version") == target.version
                and event.detail.get("credential_epoch") == delivery.credential_epoch
                and event.detail.get("delivery_uuid") == str(delivery.identifier))


def baseline_proposal(target, delivery, ids):
    if not target.version:
        if ids:
            raise Conflict("The initial baseline covers the complete snapshot.")
        return {"initial": delivery.snapshot}
    findings = list(active_findings(target).filter(pk__in=ids).order_by("pk"))
    if not ids or len(findings) != len(ids):
        raise Conflict("Select current findings to include in the proposal.")
    for finding in findings:
        current = delivery.snapshot.get(finding.resource)
        if not same(current, finding.actual) or (current is None and not confirmation(target, finding, delivery)):
            raise Conflict("The finding changed or its disappearance is unconfirmed.")
    return {"findings": [finding_proposal(finding) for finding in findings]}


def request_approval(target, actor, kind, form, now):
    delivery = basis(target, now)
    if (int(form.get("revision", "")), int(form.get("version", ""))) != (target.revision, target.version):
        raise Conflict("The observation or baseline changed. Refresh the page.")
    ticket = text_field(form.get("ticket", ""), 120, "change ticket")
    request_reason = text_field(form.get("reason", ""), 240, "request reason", required=False)
    finding = None
    execution_ref = ""
    completed_at = None
    if kind == "baseline":
        submitted = form.getlist("finding")
        if len(submitted) > 500:
            raise Conflict("Select at most 500 findings.")
        ids = {int(value) for value in submitted}
        if any(not 0 < value < 2**63 for value in ids):
            raise Conflict("Select current findings.")
        proposal = baseline_proposal(target, delivery, ids)
    else:
        from collector.contract import pilot_time

        finding_id = int(form.get("finding", ""))
        if not 0 < finding_id < 2**63:
            raise Conflict("Select a current finding.")
        finding = active_findings(target).filter(pk=finding_id).first()
        if finding is None or not same(finding.actual, delivery.snapshot.get(finding.resource)):
            raise Conflict("The finding no longer matches the complete observation.")
        execution_ref = text_field(form.get("execution_ref", ""), 240, "execution reference")
        completed_at = pilot_time(form.get("execution_completed_at", ""))
        if completed_at > now or completed_at < finding.first_seen:
            raise Conflict("Execution must have completed after the finding was opened and before this request.")
        proposal = finding_proposal(finding)
    if PilotApproval.objects.filter(target=target, kind=kind, finding=finding, state="pending").exists():
        raise Conflict("A request is already pending.")
    item = PilotApproval.objects.create(
        target=target, kind=kind, requester=actor, finding=finding, requested_at=now,
        ticket=ticket, delivery=delivery, base_version=target.version, proposal=proposal,
        proposal_hash=fingerprint(proposal), execution_ref=execution_ref, execution_completed_at=completed_at,
    )
    record(target, "approval_requested", actor, finding, approval=item.pk, kind_of_approval=kind, ticket=ticket,
           proposal_hash=item.proposal_hash, delivery=str(delivery.identifier), reason=request_reason)
    return item


def decide(target, actor, kind, approval_id, approve, reason, now):
    if not 0 < approval_id < 2**63:
        raise Conflict("Select a pending approval.")
    item = PilotApproval.objects.filter(target=target, pk=approval_id, kind=kind, state="pending").first()
    if item is None or item.requester_id == actor.pk:
        raise Conflict("A different active owner must decide this pending request.")
    reason = text_field(reason, 240, "decision reason", required=False)
    if not approve:
        decision_state(item, "rejected", now, reason, actor)
        return "Approval rejected."
    delivery = basis(target, now)
    try:
        if item.base_version != target.version or item.delivery.credential_epoch != delivery.credential_epoch:
            raise Conflict("The baseline or runner changed.")
        if kind == "baseline":
            ids = {part["id"] for part in item.proposal.get("findings", [])}
            current = baseline_proposal(target, delivery, ids)
        else:
            finding = active_findings(target).filter(pk=item.finding_id).first()
            if finding is None or not same(finding.actual, delivery.snapshot.get(finding.resource)):
                raise Conflict("The reviewed drift changed.")
            current = finding_proposal(finding)
        if fingerprint(current) != item.proposal_hash:
            raise Conflict("The proposal no longer matches the observation.")
    except Conflict as error:
        decision_state(item, "stale", now, str(error), actor)
        return str(error)
    decision_state(item, "approved", now, reason, actor)
    if kind == "baseline":
        apply_baseline(target, item, delivery, actor, now)
    return "Approval recorded."


def apply_baseline(target, approval, delivery, actor, now):
    if (approval.target_id != target.pk or approval.kind != "baseline" or approval.state != "approved"
            or approval.base_version != target.version or approval.approver_id != actor.pk):
        raise Conflict("A valid baseline approval is required.")
    if target.version:
        values = Baseline.objects.get(target=target, version=target.version).values.copy()
        ids = [part["id"] for part in approval.proposal["findings"]]
        for finding in active_findings(target).filter(pk__in=ids).order_by("pk"):
            if finding.actual is None:
                values.pop(finding.resource, None)
            else:
                values[finding.resource] = finding.actual
            finding.closed_at, finding.resolution = now, "accepted"
            finding.save(update_fields=["closed_at", "resolution"])
            record(target, "closed", actor, finding, resolution="accepted", approval=approval.pk,
                   delivery=str(delivery.identifier))
    else:
        values = approval.proposal["initial"]
    if len(values) > 500:
        raise Conflict("The approved baseline cannot exceed 500 resources.")
    target.version += 1
    Baseline.objects.create(target=target, version=target.version, values=values, actor=actor.username,
                            reason=approval.ticket, pilot_approval=approval)
    target.save(update_fields=["version"])
    active_findings(target).update(absence_delivery=None, absence_confirmation=None)
    stale_reviews(target, now, "The baseline version changed.")
    record(target, "baseline_approved", actor, version=target.version, approval=approval.pk,
           ticket=approval.ticket, approved_at=now.isoformat(), delivery=str(delivery.identifier),
           proposal_hash=approval.proposal_hash, proposal=approval.proposal)
    audit(target.workspace, actor.username, "infra_baseline_approved", target=target.name,
          version=target.version, ticket=approval.ticket, approval=approval.pk)


def eligible_review(target, finding, delivery):
    return PilotApproval.objects.filter(
        target=target, finding=finding, kind="remediation", state="approved",
        base_version=target.version, delivery__credential_epoch=delivery.credential_epoch,
        proposal_hash=fingerprint(finding_proposal(finding)),
        decided_at__lt=delivery.started_at, execution_completed_at__lt=delivery.started_at,
    ).order_by("-decided_at", "-pk").first()


def close_with_evidence(target, finding, delivery, now):
    if (delivery.target_id != target.pk or delivery.pk != target.last_complete_delivery_id
            or delivery.coverage != "complete" or pilot_intake.coverage_state(target, now) != "complete"
            or not same(delivery.snapshot.get(finding.resource), finding.expected)
            or (finding.expected is None and not confirmation(target, finding, delivery))):
        raise Conflict("Complete confirming evidence is required.")
    review = eligible_review(target, finding, delivery)
    resolution = "verified_remediation" if review else "observed_restoration"
    evidence = record(target, "closed", "collector", finding, resolution=resolution,
                      delivery=str(delivery.identifier), snapshot_hash=delivery.payload_hash,
                      started_at=delivery.started_at.isoformat(), captured_at=delivery.captured_at.isoformat(),
                      expected=finding.expected, previous=finding.actual,
                      approval=review.pk if review else None,
                      confirmation=finding.absence_confirmation_id)
    finding.closed_at = now
    finding.resolution = resolution
    finding.closure_delivery = delivery
    finding.closure_event = evidence
    finding.verified_review = review
    finding.save(update_fields=["closed_at", "resolution", "closure_delivery", "closure_event", "verified_review"])
    stale_reviews(target, now, "The finding closed.", finding)


def clear_absence(target):
    active_findings(target).update(absence_delivery=None, absence_confirmation=None)


def compare_complete(target, delivery, now):
    if not target.version:
        return "unbaselined"
    expected = Baseline.objects.get(target=target, version=target.version).values
    current = {item.resource: item for item in active_findings(target)}
    opened = 0
    awaiting = False
    for key in sorted(expected.keys() | delivery.snapshot.keys() | current.keys()):
        before, after, finding = expected.get(key), delivery.snapshot.get(key), current.get(key)
        if finding and after is None:
            if finding.absence_delivery_id is None:
                record(target, "absence_candidate", finding=finding, delivery=str(delivery.identifier))
            finding.absence_delivery = delivery
            finding.absence_confirmation = None
            finding.last_seen = now
            finding.count += 1
            finding.priority = "high"
            finding.save(update_fields=["absence_delivery", "absence_confirmation", "last_seen", "count", "priority"])
            awaiting = True
            continue
        if finding and finding.absence_delivery_id:
            record(target, "absence_invalidated", finding=finding, reason="resource_reappeared",
                   delivery=str(delivery.identifier))
            finding.absence_delivery = None
            finding.absence_confirmation = None
            finding.save(update_fields=["absence_delivery", "absence_confirmation"])
        if same(before, after):
            if finding:
                close_with_evidence(target, finding, delivery, now)
            continue
        if finding and same(finding.actual, after) and same(finding.expected, before):
            finding.count += 1
            finding.last_seen = now
            finding.save(update_fields=["count", "last_seen"])
            continue
        inherited_owner = None
        if finding:
            inherited_owner = finding.owner_id
            finding.closed_at, finding.resolution = now, "changed"
            finding.save(update_fields=["closed_at", "resolution"])
            record(target, "closed", finding=finding, resolution="changed", delivery=str(delivery.identifier))
            stale_reviews(target, now, "The observed drift changed.", finding)
        finding = Finding.objects.create(
            target=target, resource=key, expected=before, actual=after, priority=risk(before, after),
            first_seen=now, last_seen=now, baseline_version=target.version, owner_id=inherited_owner,
            absence_delivery=delivery if after is None else None,
        )
        record(target, "opened", finding=finding, delivery=str(delivery.identifier), expected=before, observed=after)
        if after is None:
            record(target, "absence_candidate", finding=finding, delivery=str(delivery.identifier))
            awaiting = True
        opened += 1
    if opened:
        alert(target, "EBS configuration drift", f"{opened} new infrastructure findings.")
    if awaiting:
        return "awaiting_confirmation"
    return "drift" if active_findings(target).exists() else "match"


def confirm_absence(target, actor, form, now):
    from collector.contract import pilot_time

    delivery = basis(target, now)
    enrollment = target.runnerenrollment
    if actor.pk == enrollment.primary_operator_id:
        raise Conflict("The corroborator must differ from the primary runner operator.")
    finding_id = int(form.get("finding", ""))
    if not 0 < finding_id < 2**63:
        raise Conflict("Select a current finding.")
    finding = active_findings(target).filter(pk=finding_id, absence_delivery=delivery).first()
    if (finding is None or delivery.pk != int(form.get("candidate_delivery_id", ""))
            or finding.resource in delivery.snapshot or confirmation(target, finding, delivery)):
        raise Conflict("The absence candidate changed or is already confirmed.")
    checked = pilot_time(form.get("checked_at", ""))
    if not delivery.captured_at <= checked <= now or now - checked > timedelta(minutes=target.interval + 5):
        raise Conflict("Check the volume independently after the latest complete observation.")
    ticket = text_field(form.get("ticket", ""), 120, "change ticket")
    ref = text_field(form.get("external_ref", ""), 240, "external evidence reference")
    if form.get("provider_absence") != "1":
        raise Conflict("An unambiguous provider absence result is required.")
    event = record(
        target, "absence_confirmed", actor, finding, ticket=ticket, external_ref=ref, checked_at=checked.isoformat(),
        account=enrollment.account, region=enrollment.region, volume_id=finding.resource.removeprefix("ebs/"),
        delivery_uuid=str(delivery.identifier), enrollment_id=str(enrollment.pk),
        credential_epoch=delivery.credential_epoch, baseline_version=target.version,
    )
    finding.absence_confirmation = event
    finding.save(update_fields=["absence_confirmation"])
    if finding.expected is None:
        close_with_evidence(target, finding, delivery, now)
    elif finding.actual is not None:
        # Preserve the open case and the previous value in the timeline.
        record(target, "absence_drift", actor, finding, previous=finding.actual, approval=None,
               delivery=str(delivery.identifier))
        finding.actual = None
        finding.priority = "high"
        finding.save(update_fields=["actual", "priority"])



def retire_target(target, actor, form, now):
    if form.get("confirm") != target.name:
        raise Conflict("Enter the exact target name to retire it.")
    ticket = text_field(form.get("ticket", ""), 120, "change ticket")
    pilot_intake.revoke_runner(target, actor, now)
    target.retired_at = now
    target.save(update_fields=["retired_at"])
    stale_reviews(target, now, "Target retired.")
    clear_absence(target)
    pilot_intake.end_gap(target, now, "retired", actor=actor)
    record(target, "target_retired", actor, ticket=ticket)
    audit(target.workspace, actor.username, "infra_target_retired", target=target.name, ticket=ticket)
