"""Finding ownership, evidence, and bounded exceptions."""
from datetime import timedelta

from django.utils import timezone

from governance.domain import audit
from governance.models import Membership
from .models import DriftException, EvidenceEvent


def record(target, kind, actor="collector", finding=None, **detail):
    return EvidenceEvent.objects.create(
        target=target, finding=finding, resource=finding.resource if finding else "",
        kind=kind, actor=actor, detail=detail,
    )


def resource_changes(expected, observed, same):
    if expected is None or observed is None:
        return []
    return [
        {"field": field, "expected": expected.get(field), "observed": observed.get(field),
         "expected_present": field in expected, "observed_present": field in observed}
        for field in sorted(expected.keys() | observed.keys())
        if (field in expected) != (field in observed) or not same(expected.get(field), observed.get(field))
    ]


def allowed_exception(change):
    # Resource additions/removals, missing fields and unknown fields remain actionable.
    return change["expected_present"] and change["observed_present"] and change["field"] in {
        "replicas", "desired_capacity", "memory_bytes", "nano_cpus",
    } and type(change["expected"]) is int and type(change["observed"]) is int


def effective_changes(expected, observed, exceptions, same):
    changes = resource_changes(expected, observed, same)
    return [change for change in changes if not (
        allowed_exception(change) and (exception := exceptions.get(change["field"])) and
        same(exception.expected, change["expected"]) and same(exception.observed, change["observed"])
    )]


def assign(target, finding, actor, owner_id):
    from .domain import Conflict

    if owner_id:
        member = Membership.objects.filter(
            workspace=target.workspace, user_id=owner_id, user__is_active=True, role__in=("owner", "operator"),
        ).select_related("user").first()
        if not member:
            raise Conflict("Choose an active workspace operator or owner.")
        assignee = member.user
    else:
        assignee = None
    if finding.owner_id != owner_id:
        finding.owner = assignee
        finding.save(update_fields=["owner"])
        record(target, "assigned", actor, finding, owner=assignee.username if assignee else "")
        audit(target.workspace, actor, "infra_finding_assigned", target=target.name, resource=finding.resource,
              owner=assignee.username if assignee else "")


def except_field(target, finding, actor, field, hours, reason):
    from .domain import Conflict, same

    change = next((item for item in resource_changes(finding.expected, finding.actual, same) if item["field"] == field), None)
    if target.platform == "terraform" or not change or not allowed_exception(change):
        raise Conflict("This field or resource change cannot be excepted.")
    if not 1 <= hours <= 168 or not reason.strip() or len(reason) > 240:
        raise Conflict("Enter a reason and a duration from 1 to 168 hours.")
    now = timezone.now()
    existing = DriftException.objects.filter(target=target, resource=finding.resource, field=field, ended_at__isnull=True)
    active = DriftException.objects.filter(target=target, ended_at__isnull=True, expires_at__gt=now)
    if not existing.filter(expires_at__gt=now).exists() and active.count() >= 100:
        raise Conflict("This target already has 100 active exceptions.")
    for old in existing:
        record(target, "exception_replaced" if old.expires_at > now else "exception_expired",
               actor, finding, field=field, exception=old.pk)
    existing.update(ended_at=now)
    exception = DriftException.objects.create(
        target=target, resource=finding.resource, field=field,
        expected=change["expected"], observed=change["observed"], expires_at=now + timedelta(hours=hours),
        actor=actor, reason=reason.strip(),
    )
    record(target, "exception_created", actor, finding, field=field, exception=exception.pk,
           expires_at=exception.expires_at.isoformat(), reason=exception.reason)
    audit(target.workspace, actor, "infra_exception_created", target=target.name, resource=finding.resource,
          field=field, exception=exception.pk)
